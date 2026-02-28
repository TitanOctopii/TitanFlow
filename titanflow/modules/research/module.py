"""TitanFlow Research Module — autonomous feed tracking and analysis."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from sqlmodel import select

from titanflow.models import FeedItem, FeedSource, GitHubRelease
from titanflow.core.http import request_with_retry
from titanflow.modules.base import BaseModule

logger = logging.getLogger("titanflow.research")

RESEARCH_SYSTEM_PROMPT = """You are TitanFlow's research analyst. Your job is to evaluate and summarize
technical news about LLMs, AI infrastructure, and developer tools.

When summarizing a feed item:
1. Write a concise 2-3 sentence summary focused on what's new and why it matters
2. Rate relevance from 0.0 to 1.0 based on:
   - 0.9-1.0: Major model release, breakthrough, or critical tool update
   - 0.7-0.8: Notable release, significant benchmark, industry shift
   - 0.5-0.6: Interesting but incremental update
   - 0.3-0.4: Tangentially related or minor
   - 0.0-0.2: Not relevant to LLM/AI infrastructure

Respond in this exact format:
SUMMARY: <your summary>
RELEVANCE: <score>"""


class ResearchModule(BaseModule):
    """Tracks RSS feeds and GitHub releases, generates LLM summaries."""

    name = "research"
    description = "Autonomous research tracking — feeds, GitHub, LLM analysis"

    def __init__(self, engine) -> None:
        super().__init__(engine)
        self._http = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "TitanFlow/0.1 Research Bot"},
        )
        self._research_config = self.config.modules.research

    async def start(self) -> None:
        """Register feeds from config and schedule fetching."""
        # Schedule periodic feed fetching
        interval = self._research_config.fetch_interval
        self.scheduler.add_interval(
            job_id="research.fetch_feeds",
            func=self.fetch_all_feeds,
            seconds=interval,
        )
        self.scheduler.add_interval(
            job_id="research.fetch_github",
            func=self.fetch_github_releases,
            seconds=interval,
        )
        self.scheduler.add_interval(
            job_id="research.process_items",
            func=self.process_unprocessed,
            seconds=600,  # every 10 minutes
        )

        self.log.info(f"Research module started — fetch interval: {interval}s")

        await self._check_feed_health()

        # Do an initial fetch on startup
        await self.fetch_all_feeds()
        await self.fetch_github_releases()

    async def stop(self) -> None:
        await self._http.aclose()
        self.scheduler.remove_job("research.fetch_feeds")
        self.scheduler.remove_job("research.fetch_github")
        self.scheduler.remove_job("research.process_items")

    async def handle_telegram(self, command: str, args: str, context: Any) -> str | None:
        if command == "research":
            return await self._cmd_research_status()
        elif command == "latest":
            return await self._cmd_latest(args)
        return None

    # ─── Feed Fetching ────────────────────────────────────

    async def fetch_all_feeds(self) -> None:
        """Fetch all registered RSS/Atom feeds."""
        self.log.info("Fetching all feeds...")
        async with self.db.session() as session:
            result = await session.exec(
                select(FeedSource).where(FeedSource.enabled == True)
            )
            sources = result.all()

        if not sources:
            self.log.info("No feed sources registered — loading from config")
            await self._load_feeds_from_config()
            async with self.db.session() as session:
                result = await session.exec(
                    select(FeedSource).where(FeedSource.enabled == True)
                )
                sources = result.all()

        new_items = 0
        for source in sources:
            count = await self._fetch_feed(source)
            new_items += count

        self.log.info(f"Feed fetch complete — {new_items} new item(s) from {len(sources)} feed(s)")

        if new_items > 0:
            await self.events.emit(
                "research.new_items",
                data={"count": new_items},
                source="research",
            )

    async def _fetch_feed(self, source: FeedSource) -> int:
        """Fetch a single feed and store new items. Returns count of new items."""
        try:
            response = await request_with_retry(self._http, "GET", source.url)
            feed = feedparser.parse(response.text)
        except Exception as e:
            self.log.warning(f"Failed to fetch feed {source.url}: {e}")
            return 0

        new_count = 0
        async with self.db.session() as session:
            for entry in feed.entries[: self._research_config.max_items_per_feed]:
                guid = entry.get("id") or entry.get("link") or hashlib.md5(
                    entry.get("title", "").encode()
                ).hexdigest()

                # Check for duplicates
                existing = await session.exec(
                    select(FeedItem).where(FeedItem.guid == guid)
                )
                if existing.first():
                    continue

                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                item = FeedItem(
                    feed_source_id=source.id,
                    guid=guid,
                    title=entry.get("title", "Untitled"),
                    url=entry.get("link", ""),
                    author=entry.get("author", ""),
                    content=entry.get("summary", entry.get("description", "")),
                    category=source.category,
                    published_at=published,
                )
                session.add(item)
                new_count += 1

            # Update last_fetched
            source.last_fetched = datetime.now(timezone.utc)
            session.add(source)
            await session.commit()

        return new_count

    async def _load_feeds_from_config(self) -> None:
        """Load feed sources from the feeds.yaml config file."""
        import yaml
        from pathlib import Path

        feeds_path = Path(self.config.config_dir) / "feeds.yaml"
        if not feeds_path.exists():
            self.log.warning(f"No feeds config at {feeds_path}")
            return

        with open(feeds_path) as f:
            data = yaml.safe_load(f) or {}

        async with self.db.session() as session:
            for section_name, feeds in data.get("feeds", {}).items():
                for feed_def in feeds:
                    url = feed_def["url"]
                    existing = await session.exec(
                        select(FeedSource).where(FeedSource.url == url)
                    )
                    if existing.first():
                        continue

                    source = FeedSource(
                        url=url,
                        name=feed_def.get("name", section_name),
                        category=feed_def.get("category", "general"),
                    )
                    session.add(source)

            await session.commit()

        self.log.info("Loaded feed sources from config")

    async def _check_feed_health(self) -> None:
        """Check feed URLs on startup and log failures."""
        import yaml
        from pathlib import Path

        feeds_path = Path(self.config.config_dir) / "feeds.yaml"
        if not feeds_path.exists():
            self.log.warning(f"No feeds config at {feeds_path}")
            return

        with open(feeds_path) as f:
            data = yaml.safe_load(f) or {}

        feed_urls: list[str] = []
        for feeds in data.get("feeds", {}).values():
            for feed_def in feeds:
                url = feed_def.get("url")
                if url:
                    feed_urls.append(url)

        if not feed_urls:
            self.log.warning("Feed health check skipped — no feeds found")
            return

        failures = 0
        for url in feed_urls:
            ok = False
            try:
                await request_with_retry(self._http, "HEAD", url, attempts=2, timeout=10.0)
                ok = True
            except Exception:
                try:
                    await request_with_retry(self._http, "GET", url, attempts=2, timeout=10.0)
                    ok = True
                except Exception as e:
                    self.log.warning(f"Feed health check failed for {url}: {e}")

            if not ok:
                failures += 1

        if failures == 0:
            self.log.info(f"Feed health check OK ({len(feed_urls)} feed(s))")

    # ─── GitHub Release Tracking ──────────────────────────

    async def fetch_github_releases(self) -> None:
        """Fetch latest releases from tracked GitHub repos."""
        import yaml
        from pathlib import Path

        repos_path = Path(self.config.config_dir) / "github_repos.yaml"
        if not repos_path.exists():
            self.log.debug("No github_repos.yaml config found")
            return

        with open(repos_path) as f:
            data = yaml.safe_load(f) or {}

        github_token = self.config.integrations.github.token
        headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        new_releases = 0
        for repo_def in data.get("tracked_repos", []):
            repo = repo_def["repo"]
            try:
                response = await request_with_retry(
                    self._http,
                    "GET",
                    f"https://api.github.com/repos/{repo}/releases",
                    headers=headers,
                    params={"per_page": 5},
                )
                releases = response.json()
            except Exception as e:
                self.log.warning(f"Failed to fetch releases for {repo}: {e}")
                continue

            async with self.db.session() as session:
                for rel in releases:
                    guid = f"{repo}:{rel['tag_name']}"
                    existing = await session.exec(
                        select(GitHubRelease).where(GitHubRelease.guid == guid)
                    )
                    if existing.first():
                        continue

                    published = None
                    if rel.get("published_at"):
                        published = datetime.fromisoformat(
                            rel["published_at"].replace("Z", "+00:00")
                        )

                    release = GitHubRelease(
                        repo=repo,
                        tag=rel["tag_name"],
                        name=rel.get("name", rel["tag_name"]),
                        body=rel.get("body", "")[:5000],  # truncate very long release notes
                        url=rel.get("html_url", ""),
                        published_at=published,
                        guid=guid,
                    )
                    session.add(release)
                    new_releases += 1

                await session.commit()

        if new_releases:
            self.log.info(f"Found {new_releases} new GitHub release(s)")
            await self.events.emit(
                "research.new_releases",
                data={"count": new_releases},
                source="research",
            )

    @staticmethod
    def _parse_llm_response(text: str) -> tuple[str, float]:
        """Parse LLM response into summary + relevance score."""
        summary = ""
        relevance = 0.5
        for line in text.strip().split("\n"):
            if line.startswith("SUMMARY:"):
                summary = line[8:].strip()
            elif line.startswith("RELEVANCE:"):
                try:
                    relevance = float(line[10:].strip())
                except ValueError:
                    relevance = 0.5
        return summary, relevance

    # ─── LLM Processing ──────────────────────────────────

    async def process_unprocessed(self) -> None:
        """Process unprocessed feed items through LLM for summarization."""
        async with self.db.session() as session:
            result = await session.exec(
                select(FeedItem)
                .where(FeedItem.is_processed == False)
                .order_by(FeedItem.fetched_at.desc())
                .limit(self.config.modules.research.processing_batch_size)
            )
            items = result.all()

        if not items:
            return

        self.log.info(f"Processing {len(items)} unprocessed feed item(s)...")

        for item in items:
            try:
                prompt = f"""Evaluate this feed item:

Title: {item.title}
Category: {item.category}
Content: {item.content[:2000]}

{RESEARCH_SYSTEM_PROMPT}"""

                response = await self.llm.generate(
                    prompt,
                    temperature=0.3,
                    max_tokens=500,
                )

                summary, relevance = self._parse_llm_response(response)

                async with self.db.session() as session:
                    item.summary = summary
                    item.relevance_score = relevance
                    item.is_processed = True
                    session.add(item)
                    await session.commit()

            except Exception as e:
                self.log.warning(f"Failed to process item '{item.title}': {e}")

        self.log.info(f"Processed {len(items)} item(s)")

    # ─── Telegram Commands ────────────────────────────────

    async def _cmd_research_status(self) -> str:
        async with self.db.session() as session:
            feeds = (await session.exec(select(FeedSource))).all()
            items = (await session.exec(
                select(FeedItem).where(FeedItem.is_processed == True)
            )).all()
            unprocessed = (await session.exec(
                select(FeedItem).where(FeedItem.is_processed == False)
            )).all()

        lines = [
            "📊 Research Module Status",
            f"  Feeds: {len(feeds)}",
            f"  Processed items: {len(items)}",
            f"  Pending: {len(unprocessed)}",
        ]
        return "\n".join(lines)

    async def _cmd_latest(self, args: str) -> str:
        """Get latest high-relevance items."""
        limit = 5
        async with self.db.session() as session:
            result = await session.exec(
                select(FeedItem)
                .where(FeedItem.is_processed == True)
                .where(FeedItem.relevance_score >= 0.6)
                .order_by(FeedItem.fetched_at.desc())
                .limit(limit)
            )
            items = result.all()

        if not items:
            return "No high-relevance items found yet. Research engine is still gathering data."

        lines = ["📰 Latest Research Items\n"]
        for item in items:
            score = f"[{item.relevance_score:.1f}]"
            lines.append(f"• {score} {item.title}")
            if item.summary:
                lines.append(f"  {item.summary[:150]}")
            lines.append("")

        return "\n".join(lines)
