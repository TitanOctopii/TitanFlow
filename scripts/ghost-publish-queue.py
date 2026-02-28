#!/usr/bin/env python3
"""
ghost-publish-queue.py
One-shot publisher: reads docs/ghost-post-drafts.md and publishes all
posts to Ghost via the Admin API.

Usage:
    python3 scripts/ghost-publish-queue.py [--dry-run] [--post N]

Requires:
    GHOST_ADMIN_KEY_FLOW env var (format: kid:hex_secret)
    GHOST_API_URL env var OR defaults to https://titanflow.space

Options:
    --dry-run     Parse drafts and print what would be published. No API calls.
    --post N      Publish only post number N (1-indexed). Default: all.
    --delay N     Seconds to wait between posts (default: 3).
    --status STR  Ghost post status: published or draft (default: published).
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DRAFTS_FILE = Path(__file__).parent.parent / "docs" / "ghost-post-drafts.md"
DEFAULT_API_URL = "https://titanflow.space"
DEFAULT_DELAY = 3  # seconds between posts


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Post:
    number: int
    title: str
    tags: list[str]
    content: str  # raw markdown/text

    def __str__(self) -> str:
        return f"POST {self.number}: {self.title} [{', '.join(self.tags)}]"


# ---------------------------------------------------------------------------
# Draft parser
# ---------------------------------------------------------------------------

def parse_drafts(path: Path) -> list[Post]:
    """Parse docs/ghost-post-drafts.md into a list of Post objects."""
    text = path.read_text()
    posts: list[Post] = []

    # Split on ## POST N — headers
    sections = re.split(r"^## POST \d+", text, flags=re.MULTILINE)
    headers = re.findall(r"^## POST (\d+)", text, flags=re.MULTILINE)

    if not headers:
        sys.exit(f"ERROR: No posts found in {path}")

    for i, (num_str, section) in enumerate(zip(headers, sections[1:]), start=1):
        num = int(num_str)
        lines = section.strip().splitlines()

        # Extract title
        title = ""
        title_idx = -1
        for j, line in enumerate(lines):
            m = re.match(r"\*\*Title:\*\*\s*(.+)", line)
            if m:
                title = m.group(1).strip()
                title_idx = j
                break

        # Extract tags
        tags: list[str] = []
        for line in lines:
            m = re.match(r"\*\*Tags:\*\*\s*(.+)", line)
            if m:
                tags = [t.strip() for t in m.group(1).split(",")]
                break

        # Extract content block (everything after **Content:**)
        content = ""
        in_content = False
        content_lines: list[str] = []
        for line in lines:
            if re.match(r"\*\*Content:\*\*", line):
                in_content = True
                continue
            if in_content:
                # Stop at next --- separator
                if line.strip() == "---":
                    break
                content_lines.append(line)
        content = "\n".join(content_lines).strip()

        if title and content:
            posts.append(Post(number=num, title=title, tags=tags, content=content))
        else:
            print(f"  WARNING: POST {num} missing title or content — skipped")

    return posts


# ---------------------------------------------------------------------------
# Ghost JWT
# ---------------------------------------------------------------------------

def make_jwt(admin_key: str) -> str:
    """Generate a Ghost Admin API JWT token. admin_key format: kid:hex_secret"""
    kid, secret_hex = admin_key.split(":", 1)
    secret_bytes = bytes.fromhex(secret_hex)

    def b64(data: dict) -> bytes:
        return base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":")).encode()
        ).rstrip(b"=")

    iat = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    payload = {"iat": iat, "exp": iat + 300, "aud": "/admin/"}

    signing_input = b64(header) + b"." + b64(payload)
    sig = hmac.new(secret_bytes, signing_input, hashlib.sha256).digest()
    token = (signing_input + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode()
    return token


# ---------------------------------------------------------------------------
# HTML conversion
# ---------------------------------------------------------------------------

def to_html(text: str) -> str:
    """Convert plain text / light markdown to Ghost-compatible HTML."""
    lines = text.splitlines()
    html_parts: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    for line in lines:
        # Fenced code blocks
        if line.startswith("```"):
            if in_code_block:
                code = "\n".join(code_lines)
                html_parts.append(f"<pre><code>{_escape(code)}</code></pre>")
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # ATX headings
        m = re.match(r"^(#{1,4})\s+(.+)", line)
        if m:
            level = min(len(m.group(1)) + 1, 4)  # shift h1→h2, h2→h3, etc.
            html_parts.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        # Horizontal rules
        if re.match(r"^---+$", line.strip()):
            html_parts.append("<hr>")
            continue

        # Bullet list items
        m = re.match(r"^[-*]\s+(.+)", line)
        if m:
            html_parts.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # Numbered list items
        m = re.match(r"^\d+\.\s+(.+)", line)
        if m:
            html_parts.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # Blockquote
        m = re.match(r"^>\s*(.+)", line)
        if m:
            html_parts.append(f"<blockquote><p>{_inline(m.group(1))}</p></blockquote>")
            continue

        # Empty line
        if not line.strip():
            html_parts.append("")
            continue

        # Regular paragraph line
        html_parts.append(f"<p>{_inline(line)}</p>")

    # Close any unclosed code block
    if in_code_block and code_lines:
        code = "\n".join(code_lines)
        html_parts.append(f"<pre><code>{_escape(code)}</code></pre>")

    return "\n".join(html_parts)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    """Apply inline markdown: bold, italic, code, links."""
    # Bold **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic *text* (avoid matching **)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    # Inline code `text`
    text = re.sub(r"`(.+?)`", lambda m: f"<code>{_escape(m.group(1))}</code>", text)
    # Links [text](url)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text


# ---------------------------------------------------------------------------
# Ghost publish
# ---------------------------------------------------------------------------

def publish_post(
    post: Post,
    admin_key: str,
    api_url: str,
    status: str = "published",
) -> str:
    """Publish a single post to Ghost. Returns the live URL."""
    token = make_jwt(admin_key)
    html = to_html(post.content)

    tag_objects = [{"name": t} for t in post.tags]

    payload = json.dumps({
        "posts": [{
            "title": post.title,
            "html": html,
            "status": status,
            "tags": tag_objects,
            "visibility": "public",
        }]
    }).encode()

    endpoint = f"{api_url.rstrip('/')}/ghost/api/admin/posts/?source=html"

    req = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json",
            "Accept-Version": "v5.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            url = body["posts"][0].get("url", "(no url returned)")
            return url
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection failed: {e.reason}")


# ---------------------------------------------------------------------------
# State tracking (avoid re-publishing on re-run)
# ---------------------------------------------------------------------------

STATE_FILE = Path.home() / ".titanflow" / "ghost-publish-queue-state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"published": {}}  # key: post title, value: url


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish Ghost post drafts from docs/ghost-post-drafts.md"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print posts without publishing")
    parser.add_argument("--post", type=int, default=None, metavar="N", help="Publish only post N (1-indexed)")
    parser.add_argument("--delay", type=int, default=DEFAULT_DELAY, metavar="SEC", help=f"Seconds between posts (default: {DEFAULT_DELAY})")
    parser.add_argument("--status", default="published", choices=["published", "draft"], help="Ghost post status (default: published)")
    parser.add_argument("--force", action="store_true", help="Re-publish posts that were already published")
    args = parser.parse_args()

    # Env
    admin_key = os.environ.get("GHOST_ADMIN_KEY_FLOW", "")
    api_url = os.environ.get("GHOST_API_URL", DEFAULT_API_URL)

    if not args.dry_run and not admin_key:
        sys.exit(
            "ERROR: GHOST_ADMIN_KEY_FLOW not set.\n"
            "Add it to your environment: export GHOST_ADMIN_KEY_FLOW=kid:hex_secret\n"
            "Or add it to your launchd plist EnvironmentVariables."
        )

    # Parse
    if not DRAFTS_FILE.exists():
        sys.exit(f"ERROR: Drafts file not found: {DRAFTS_FILE}")

    posts = parse_drafts(DRAFTS_FILE)
    print(f"\n📰 Found {len(posts)} posts in {DRAFTS_FILE.name}")

    # Filter to single post if requested
    if args.post is not None:
        posts = [p for p in posts if p.number == args.post]
        if not posts:
            sys.exit(f"ERROR: No post with number {args.post} found.")

    # Load state
    state = load_state()
    already_published = state.get("published", {})

    # Print summary
    print(f"   API URL  : {api_url}")
    print(f"   Status   : {args.status}")
    print(f"   Dry run  : {args.dry_run}")
    print(f"   Force    : {args.force}")
    print()

    published_count = 0
    skipped_count = 0
    error_count = 0

    for i, post in enumerate(posts):
        title_key = post.title

        if not args.force and title_key in already_published:
            print(f"  ⏭  [{post.number}] SKIPPED (already published): {post.title}")
            print(f"        URL: {already_published[title_key]}")
            skipped_count += 1
            continue

        print(f"  📤 [{post.number}] {post.title}")
        print(f"       Tags: {', '.join(post.tags)}")
        print(f"       Content: {len(post.content)} chars")

        if args.dry_run:
            # Show a preview of the HTML
            html_preview = to_html(post.content)[:200].replace("\n", " ")
            print(f"       HTML preview: {html_preview}...")
            print()
            continue

        try:
            url = publish_post(post, admin_key, api_url, status=args.status)
            print(f"       ✅ Published: {url}")
            already_published[title_key] = url
            state["published"] = already_published
            save_state(state)
            published_count += 1
        except RuntimeError as e:
            print(f"       ❌ ERROR: {e}")
            error_count += 1

        # Delay between posts
        if i < len(posts) - 1 and args.delay > 0:
            time.sleep(args.delay)

        print()

    # Summary
    print("─" * 50)
    if args.dry_run:
        print(f"DRY RUN complete. {len(posts)} post(s) would be published.")
    else:
        print(f"Done. Published: {published_count}  Skipped: {skipped_count}  Errors: {error_count}")
        if published_count > 0:
            print(f"State saved to: {STATE_FILE}")
    print()


if __name__ == "__main__":
    main()
