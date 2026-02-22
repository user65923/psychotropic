from io import BytesIO
from operator import itemgetter
from urllib.parse import quote

import httpx
from PIL import Image


PNWIKI_URL = 'https://psychonautwiki.org/w/'

PNWIKI_MW_API_URL = 'https://psychonautwiki.org/w/api.php'

PNWIKI_API_URL = 'https://api.psychonautwiki.org/'


class PNWikiAPIClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        super().__init__(
            base_url=PNWIKI_API_URL,
            headers={
                "accept-type": "application/json",
                "content-type": "application/json"
            },
            *args, **kwargs
        )
    
    async def post_graphql(self, query, *args, **kwargs):
        return await self.post(
            url='/',
            json={'query': query},
            *args, **kwargs
        )


async def list_substances():
    query = """
        {
            substances(limit: 1000) {
                name
            }
        }
    """
    async with PNWikiAPIClient() as client:
        r = await client.post_graphql(query)
    
    return list(map(
        itemgetter('name'),
        r.json()['data']['substances']
    ))


async def get_substance(query, **kwargs):
    query = """
        {
            substances(query: "%s", limit: 1) {
                name
                url
                class {
                    chemical
                    psychoactive
                }
            }
        }
    """ % query

    async with PNWikiAPIClient() as client:
        r = await client.post_graphql(query, **kwargs)

    substances = r.json()["data"]["substances"]

    return substances[0] if len(substances) else None


async def get_page_images(substance_names):
    """Batch-query the MediaWiki API to get the primary image filename
    for each substance page.

    Returns a dict mapping substance name to its SVG filename, or None
    if no page image was found.
    """
    result = {}

    async with httpx.AsyncClient() as client:
        # MediaWiki API supports up to 50 titles per request
        for i in range(0, len(substance_names), 50):
            batch = substance_names[i:i + 50]
            titles = "|".join(batch)

            r = await client.get(PNWIKI_MW_API_URL, params={
                "action": "query",
                "titles": titles,
                "prop": "pageimages",
                "format": "json",
            })

            pages = r.json().get("query", {}).get("pages", {})
            for page in pages.values():
                title = page.get("title")
                pageimage = page.get("pageimage")
                if title and pageimage:
                    result[title] = pageimage

    return result


def get_schematic_url(filename, width=500):
    return (
        f'{PNWIKI_URL}thumb.php'
        f'?f={quote(filename)}&width={width}'
    )


async def get_schematic_image(filename, width=500, background_color=None):
    """Get a PIL `Image` of a substance by fetching its schematic on
    PNWiki. `filename` is the actual SVG filename from the wiki.
    Return `None` if no schematic is found."""
    async with httpx.AsyncClient() as client:
        r = await client.get(get_schematic_url(filename, width))

    if r.status_code != 200:
        return None

    image = Image.open(BytesIO(r.content))

    if background_color:
        background = Image.new("RGB", image.size, background_color)
        background.paste(image, mask=image)
        image = background

    return image
