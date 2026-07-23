import os

import httpx
from bs4 import BeautifulSoup
import asyncio
import logging
import zipfile

from config import Config


logger = logging.getLogger(__name__)


def _get_download_page(url: str) -> str:
    logger.debug(f"Fetching download page from {url}")
    response = httpx.get(url)
    return response.text


def get_urls_from_page(root_url: str) -> list[str]:

    download_page = _get_download_page(root_url)
    soup = BeautifulSoup(download_page, 'html.parser')

    urls = []
    links = soup.find_all('a')
    logger.debug(f"Found {len(links)} links on download page")

    for link in links:
        href = str(link.get('href'))
        if href.endswith('.zip'):
            urls.append(os.path.join(root_url, href))

    return urls

async def download_zips(urls: list[str], data_folder: str = Config.SAVE_PATH) -> None:

    logger.debug(f"Downloading {len(urls)} zips")
    zips_folder = os.path.join(data_folder, "zips")
    os.makedirs(zips_folder, exist_ok=True)

    async with httpx.AsyncClient() as client:

        tasks = [asyncio.create_task(client.get(url)) for url in urls]
        responses = await asyncio.gather(*tasks)

    for response, url in zip(responses, urls):
        zip_path = os.path.join(zips_folder, os.path.basename(url))

        extract_dir = os.path.join(data_folder, os.path.basename(url).removesuffix('.zip'))
        os.makedirs(extract_dir, exist_ok=True)

        with open(zip_path, 'wb') as f:
            f.write(response.content)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        os.remove(zip_path)

    logger.debug(f"Extracted {len(urls)} zips to {data_folder}")
