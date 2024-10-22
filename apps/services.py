import asyncio
from typing import AsyncGenerator, List

from fake_useragent import UserAgent
from requests_html import HTML, AsyncHTMLSession

from apps.tools import curl


class ImageExtractor:
    _instance = None

    def __new__(cls, *args, **kwargs):
        """Implement Singleton pattern"""
        if cls._instance is None:
            cls._instance = super(ImageExtractor, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        """Initialize ImageExtractor with necessary attributes"""
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self.origin = "https://se8.us"
            self.cli = AsyncHTMLSession()
            self.cli.headers.update(
                {
                    "User-Agent": UserAgent(os=["windows"], platforms="pc").chrome,
                    "Accept-Language": "en-GB,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                    "Cache-Control": "max-age=0",
                    "Dnt": "1",
                    "Priority": "u=0, i",
                }
            )
            self.max_page = 2000

    async def _send_request(self, url: str, use_curl: bool = True) -> object:
        """Send a GET request to the given URL"""
        url = url.strip()

        if use_curl:
            resp = await asyncio.to_thread(curl, url)
            return HTML(html=resp)

        return await self.cli.get(url=url, headers={"referer": "https://se8.us/"})

    async def get_max_page(self) -> int:
        """Fetch the maximum page number"""
        try:
            resp = await self._send_request(f"{self.origin}/index.php/category/page/1")
            self.max_page = int(resp.xpath('//a[@class="end"]/@href')[0].split("/")[-1])
            print(f"Max page: {self.max_page}")
        except Exception as e:
            print(e)

    async def get_books(self, target_page: int = None) -> AsyncGenerator[str, None]:
        """Fetch books from the website"""

        page_range = (
            range(1, self.max_page + 1)
            if not target_page
            else range(target_page, target_page + 1)
        )

        for page in page_range:
            print(f"Fetching page {page}")
            resp = await self._send_request(
                f"{self.origin}/index.php/category/page/{page}"
            )
            if not (books := resp.xpath("//div[@class='common-comic-item']")):
                break

            for book in books:
                yield {
                    "raw_url": (url := book.xpath('//a[@class="cover"]/@href')[0]),
                    "id": url.split("/")[-1],
                    "title": book.xpath('//p[@class="comic__title"]')[0].text,
                    "image_url": book.xpath("//img/@data-original")[0],
                    "current": book.xpath("//p[@class='comic-update']/a/text()")[0],
                }

    async def get_episodes(self, url: str) -> AsyncGenerator[str, None]:
        """Fetch episodes for a specific book"""
        resp = await self._send_request(url)
        if not (
            episodes := resp.xpath("//ul[@class='chapter__list-box clearfix']//li")
        ):
            return

        yield {
            "tags": resp.xpath("//div[@class='comic-status']//a/text()"),
            "hot": float(
                resp.xpath("//div[@class='comic-status']/span[3]/b/text()")[0].split()[
                    0
                ]
            ),
            "description": resp.xpath("//div[@class='comic-intro']//p")[2].text,
        }

        for episode in episodes:
            yield {
                "raw_url": (url := episode.xpath("//a/@href")[0]),
                "id": url.split("/")[-1],
                "title": episode.text,
            }

    async def get_images(self, url: str) -> AsyncGenerator:
        """Fetch images for a specific episode"""
        resp = await self._send_request(url)
        if not (images := resp.xpath("//div[@class='rd-article__pic hide']")):
            return

        for image_div in images:
            yield {
                "id": image_div.xpath("//@data-pid")[0],
                "index": image_div.xpath("//@data-index")[0],
                "raw_url": image_div.xpath("//img/@data-original")[0],
            }

    async def download_image(self, url: str, key: str = None) -> str:
        """Download and encode image from the given URL"""
        resp = await self._send_request(url, use_curl=False)
        if not resp.ok:
            return ""
        if not resp.headers.get("Content-Type", "").startswith("image"):
            return ""
        if key:
            return [key, resp.content]
        return resp.content

    async def get_images_concurrently(self, urls: List[str]) -> List[str]:
        """Fetch images concurrently"""
        tasks = [self.download_image(url) for url in urls]
        return await asyncio.gather(*tasks)

    async def get_images_concurrently_with_id(self, items: dict) -> List[str]:
        """Fetch images concurrently"""
        tasks = [self.download_image(url=url, key=key) for (key, url) in items]
        return await asyncio.gather(*tasks)
