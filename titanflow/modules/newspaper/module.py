"""TitanFlow Newspaper Module — autonomous publishing to titanflow.space."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt
from sqlmodel import select

from titanflow.models import Article, FeedItem, GitHubRelease
from titanflow.modules.base import BaseModule

logger = logging.getLogger("titanflow.newspaper")

ARTICLE_SYSTEM_PROMPT = """You are the editor of TitanFlow Daily, an autonomous AI research newspaper
published at titanflow.space. You write clear, technically accurate articles about LLM releases,
AI infrastructure tools, and developer ecosystem changes.

Your writing style:
- Technical but accessible — a senior engineer should find it useful, a curious beginner should learn from it
- No hype, no fluff — facts, analysis, implications
- Include specific version numbers, benchmark results, and technical details when available
- Brief analysis of what each development means for practitioners
- Written from TitanArray's perspective — a homelab AI infrastructure

Format your articles in clean Markdown with appropriate headers."""

BRIEFING_PROMPT = """Write a "Morning Briefing" article summarizing these overnight developments.
The article should have:
1. A compelling headline
2. A 1-2 sentence lead
3. Each item as a section with analysis
4. A "What to Watch" closing section

Items to cover:
{items}

Respond in this exact format:
HEADLINE: <headline>
SLUG: <url-slug>
EXCERPT: <1-2 sentence excerpt>
CONTENT:
<full markdown article>"""

DIGEST_PROMPT = """Write an "Evening Digest" article summarizing today's key developments.
Focus on synthesis — how do these pieces connect? What patterns are emerging?

Items to cover:
{items}

Respond in this exact format:
HEADLINE: <headline>
SLUG: <url-slug>
EXCERPT: <1-2 sentence excerpt>
CONTENT:
<full markdown article>"""

WEEKLY_PROMPT = """Write a comprehensive "Week in Review" article covering this week's most significant
developments in LLMs and AI infrastructure. This is the flagship weekly piece.

Structure:
1. "The Big Picture" — 2-3 paragraph overview of the week's themes
2. "Model Releases" — new models, benchmarks, what they mean
3. "Tools & Infrastructure" — updates to Ollama, llama.cpp, vLLM, etc.
4. "Research Highlights" — notable papers simplified
5. "From the Array" — TitanArray's own experiments (if any items tagged from_the_array)
6. "Looking Ahead" — what to watch next week

Items to cover:
{items}

Respond in this exact format:
HEADLINE: <headline>
SLUG: <url-slug>
EXCERPT: <1-2 sentence excerpt>
CONTENT:
<full markdown article>"""


class NewspaperModule(BaseModule):
    """Autonomous newspaper publisher for titanflow.space via Ghost CMS."""

    name = "newspaper"
    description = "Autonomous LLM/AI newspaper publishing to titanflow.space"

    def __init__(self, engine) -> None:
        super().__init__(engine)
        self._http = httpx.AsyncClient(timeout=60.0)
        self._newspaper_config = self.config.modules.newspaper
        self._ghost_config = self.config.integrations.ghost.titanflow

    async def start(self) -> None:
        """Schedule autonomous publishing."""
        # Parse morning briefing time
        morning = self._newspaper_config.morning_briefing.split(":")
        self.scheduler.add_cron(
            job_id="newspaper.morning_briefing",
            func=self.publish_morning_briefing,
            hour=int(morning[0]),
            minute=int(morning[1]),
        )

        # Parse evening digest time
        evening = self._newspaper_config.evening_digest.split(":")
        self.scheduler.add_cron(
            job_id="newspaper.evening_digest",
            func=self.publish_evening_digest,
            hour=int(evening[0]),
            minute=int(evening[1]),
        )

        # Weekly review — Sunday 8 AM ET
        self.scheduler.add_cron(
            job_id="newspaper.weekly_review",
            func=self.publish_weekly_review,
            day_of_week="sun",
            hour=8,
            minute=0,
        )

        # Listen for breaking news events
        self.events.subscribe("research.new_releases", self._on_new_releases)

        self.log.info("Newspaper module started — publishing schedule active")

    async def stop(self) -> None:
        await self._http.aclose()
        self.scheduler.remove_job("newspaper.morning_briefing")
        self.scheduler.remove_job("newspaper.evening_digest")
        self.scheduler.remove_job("newspaper.weekly_review")

    async def handle_telegram(self, command: str, args: str, context: Any) -> str | None:
        if command == "newspaper":
            return await self._cmd_newspaper_status()
        elif command == "publish":
            return await self._cmd_force_publish(args)
        return None

    # ─── Publishing Pipelines ─────────────────────────────

    async def publish_morning_briefing(self) -> None:
        """Generate and publish the morning briefing."""
        self.log.info("Generating morning briefing...")
        items = await self._get_unpublished_items(min_relevance=0.5, limit=15)
        if not items:
            self.log.info("No new items for morning briefing — skipping")
            return

        await self._generate_and_publish(
            prompt_template=BRIEFING_PROMPT,
            items=items,
            article_type="briefing",
            category="daily",
        )

    async def publish_evening_digest(self) -> None:
        """Generate and publish the evening digest."""
        self.log.info("Generating evening digest...")
        items = await self._get_unpublished_items(min_relevance=0.5, limit=15)
        if not items:
            self.log.info("No new items for evening digest — skipping")
            return

        await self._generate_and_publish(
            prompt_template=DIGEST_PROMPT,
            items=items,
            article_type="digest",
            category="daily",
        )

    async def publish_weekly_review(self) -> None:
        """Generate and publish the weekly review."""
        self.log.info("Generating weekly review...")
        # Get all processed items from the past week
        items = await self._get_week_items(min_relevance=0.4)
        if not items:
            self.log.info("No items for weekly review — skipping")
            return

        await self._generate_and_publish(
            prompt_template=WEEKLY_PROMPT,
            items=items,
            article_type="weekly",
            category="weekly",
        )

    # ─── Core Generation + Publishing ─────────────────────

    async def _generate_and_publish(
        self,
        prompt_template: str,
        items: list[dict],
        article_type: str,
        category: str,
    ) -> Article | None:
        """Generate article via LLM and publish to Ghost."""
        # Format items for the prompt
        items_text = "\n\n".join(
            f"- [{item['category']}] {item['title']}\n  {item.get('summary', item.get('content', '')[:300])}"
            for item in items
        )

        prompt = prompt_template.format(items=items_text)

        try:
            response = await self.llm.generate(
                prompt,
                system=ARTICLE_SYSTEM_PROMPT,
                temperature=0.6,
                max_tokens=4096,
            )
        except Exception as e:
            self.log.error(f"LLM generation failed: {e}")
            return None

        # Parse response
        headline = ""
        slug = ""
        excerpt = ""
        content = ""
        in_content = False

        for line in response.split("\n"):
            if line.startswith("HEADLINE:"):
                headline = line[9:].strip()
            elif line.startswith("SLUG:"):
                slug = line[5:].strip()
            elif line.startswith("EXCERPT:"):
                excerpt = line[8:].strip()
            elif line.startswith("CONTENT:"):
                in_content = True
            elif in_content:
                content += line + "\n"

        if not headline or not content.strip():
            self.log.error("Failed to parse article from LLM response")
            return None

        # Ensure unique slug
        if not slug:
            slug = headline.lower().replace(" ", "-")[:60]
        slug = f"{datetime.now().strftime('%Y-%m-%d')}-{slug}"

        # Store article
        article = Article(
            title=headline,
            slug=slug,
            content_markdown=content.strip(),
            excerpt=excerpt,
            category=category,
            article_type=article_type,
            source_item_ids=",".join(str(item.get("id", "")) for item in items),
        )

        # Publish to Ghost if enabled
        if self._newspaper_config.auto_publish and self._ghost_config.enabled:
            ghost_id = await self._publish_to_ghost(article)
            if ghost_id:
                article.ghost_post_id = ghost_id
                article.status = "published"
                article.published_at = datetime.now(timezone.utc)
                self.log.info(f"Published: {headline}")

                # Mark source items as published
                await self._mark_items_published(items)
            else:
                article.status = "failed"
                self.log.error(f"Failed to publish to Ghost: {headline}")
        else:
            article.status = "draft"

        # Save to database
        async with self.db.session() as session:
            session.add(article)
            await session.commit()

        await self.events.emit(
            "newspaper.article_created",
            data={"title": headline, "type": article_type, "status": article.status},
            source="newspaper",
        )

        return article

    # ─── Ghost CMS Integration ────────────────────────────

    async def _publish_to_ghost(self, article: Article) -> str | None:
        """Publish an article to Ghost CMS. Returns ghost post ID or None."""
        admin_key = self._ghost_config.admin_key
        if not admin_key or ":" not in admin_key:
            self.log.error("Ghost admin key not configured or invalid format (need id:secret)")
            return None

        ghost_url = self._ghost_config.url.rstrip("/")

        try:
            # Generate Ghost Admin API JWT
            key_id, secret = admin_key.split(":")
            iat = int(datetime.now(timezone.utc).timestamp())
            header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
            payload = {
                "iat": iat,
                "exp": iat + 300,  # 5 min expiry
                "aud": "/admin/",
            }
            token = jwt.encode(payload, bytes.fromhex(secret), algorithm="HS256", headers=header)

            # Convert markdown to mobiledoc (Ghost's format)
            mobiledoc = json.dumps({
                "version": "0.3.1",
                "atoms": [],
                "cards": [["markdown", {"markdown": article.content_markdown}]],
                "markups": [],
                "sections": [[10, 0]],
            })

            post_data = {
                "posts": [{
                    "title": article.title,
                    "slug": article.slug,
                    "mobiledoc": mobiledoc,
                    "custom_excerpt": article.excerpt,
                    "status": "published",
                    "tags": [
                        {"name": article.category},
                        {"name": article.article_type},
                        {"name": "auto-generated"},
                    ],
                }]
            }

            response = await self._http.post(
                f"{ghost_url}/ghost/api/admin/posts/",
                headers={
                    "Authorization": f"Ghost {token}",
                    "Content-Type": "application/json",
                },
                json=post_data,
            )
            response.raise_for_status()
            result = response.json()
            return result["posts"][0]["id"]

        except Exception as e:
            self.log.error(f"Ghost publishing error: {e}")
            return None

    # ─── Data Helpers ─────────────────────────────────────

    async def _get_unpublished_items(
        self, min_relevance: float = 0.5, limit: int = 15
    ) -> list[dict]:
        """Get processed, unpublished feed items above relevance threshold."""
        async with self.db.session() as session:
            result = await session.exec(
                select(FeedItem)
                .where(FeedItem.is_processed == True)
                .where(FeedItem.is_published == False)
                .where(FeedItem.relevance_score >= min_relevance)
                .order_by(FeedItem.relevance_score.desc())
                .limit(limit)
            )
            items = result.all()

            # Also get unpublished GitHub releases
            releases = await session.exec(
                select(GitHubRelease)
                .where(GitHubRelease.is_published == False)
                .order_by(GitHubRelease.published_at.desc())
                .limit(5)
            )
            gh_items = releases.all()

        combined = []
        for item in items:
            combined.append({
                "id": item.id,
                "type": "feed",
                "title": item.title,
                "summary": item.summary,
                "content": item.content[:500],
                "category": item.category,
                "url": item.url,
            })
        for rel in gh_items:
            combined.append({
                "id": rel.id,
                "type": "github",
                "title": f"{rel.repo} {rel.tag}: {rel.name}",
                "summary": rel.body[:500],
                "content": rel.body[:500],
                "category": "tools",
                "url": rel.url,
            })

        return combined

    async def _get_week_items(self, min_relevance: float = 0.4) -> list[dict]:
        """Get all processed items from the past week."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        async with self.db.session() as session:
            result = await session.exec(
                select(FeedItem)
                .where(FeedItem.is_processed == True)
                .where(FeedItem.relevance_score >= min_relevance)
                .where(FeedItem.fetched_at >= cutoff)
                .order_by(FeedItem.relevance_score.desc())
                .limit(30)
            )
            items = result.all()

        return [
            {
                "id": item.id,
                "type": "feed",
                "title": item.title,
                "summary": item.summary,
                "content": item.content[:500],
                "category": item.category,
                "url": item.url,
            }
            for item in items
        ]

    async def _mark_items_published(self, items: list[dict]) -> None:
        """Mark source items as published."""
        async with self.db.session() as session:
            for item in items:
                if item["type"] == "feed" and item.get("id"):
                    result = await session.exec(
                        select(FeedItem).where(FeedItem.id == item["id"])
                    )
                    feed_item = result.first()
                    if feed_item:
                        feed_item.is_published = True
                        session.add(feed_item)
                elif item["type"] == "github" and item.get("id"):
                    result = await session.exec(
                        select(GitHubRelease).where(GitHubRelease.id == item["id"])
                    )
                    release = result.first()
                    if release:
                        release.is_published = True
                        session.add(release)
            await session.commit()

    # ─── Event Handlers ───────────────────────────────────

    async def _on_new_releases(self, event) -> None:
        """Handle new GitHub releases — check for breaking news."""
        # Could trigger a "breaking" article for major releases
        count = event.data.get("count", 0)
        if count >= 3:
            self.log.info(f"Multiple new releases detected ({count}) — consider breaking article")

    # ─── Telegram Commands ────────────────────────────────

    async def _cmd_newspaper_status(self) -> str:
        async with self.db.session() as session:
            articles = (await session.exec(select(Article))).all()
            published = [a for a in articles if a.status == "published"]
            drafts = [a for a in articles if a.status == "draft"]

        lines = [
            "📰 Newspaper Status",
            f"  Published: {len(published)}",
            f"  Drafts: {len(drafts)}",
            f"  Auto-publish: {'ON' if self._newspaper_config.auto_publish else 'OFF'}",
            f"  Morning briefing: {self._newspaper_config.morning_briefing} ET",
            f"  Evening digest: {self._newspaper_config.evening_digest} ET",
        ]
        return "\n".join(lines)

    async def _cmd_force_publish(self, args: str) -> str:
        """Force a publish cycle."""
        if args == "briefing":
            await self.publish_morning_briefing()
            return "Morning briefing generation triggered."
        elif args == "digest":
            await self.publish_evening_digest()
            return "Evening digest generation triggered."
        elif args == "weekly":
            await self.publish_weekly_review()
            return "Weekly review generation triggered."
        return "Usage: /publish briefing|digest|weekly"
