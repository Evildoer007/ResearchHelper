"""Registered public MIIT bulletin directories and their dated article links."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from .contracts import ContractError, timepoint, unique_json_object

HOST = 'www.miit.gov.cn'
ORIGIN = 'https://' + HOST
UNIT_PATH = '/api-gateway/jpaas-publish-server/front/page/build/unit'
POLICY = 'official_miit_channels_version'
MAX_PAGE = 5
CHANNELS = {
    'miit_electronics': dict(name='工信部电子信息司', path='/gxsj/tjfx/dzxx',
        page_id='53a9ffceae214c61a17d41fddb731804', tag='右侧内容'),
    'miit_vehicles': dict(name='工信部装备工业一司', path='/jgsj/zbys/qcgy',
        page_id='c52d8862a87d454582ed7c46513978d9', tag='当前栏目_list'),
}


def catalogue_address(channel, page):
    if not isinstance(page, str) or not re.fullmatch(r'[0-5]', page):
        raise ContractError('工信部目录页码须在0至5之间')
    profile = CHANNELS[channel]
    query = dict(parseType='buildstatic', webId='8d828e408d90447786ddbe128d495e9e',
                 tplSetId='209741b2109044b5b7695700b2bec37e', pageType='column',
                 tagId=profile['tag'], editType='null', pageId=profile['page_id'],
                 paramJson=json.dumps(dict(pageNo=int(page) + 1, pageSize='24'), separators=(',', ':')))
    return ORIGIN + UNIT_PATH + '?' + urlencode(query)


def is_catalogue_address(value):
    return any(value == catalogue_address(channel, str(page))
               for channel in CHANNELS for page in range(MAX_PAGE + 1))


def article_address(value, channel):
    parsed = urlsplit(value)
    path = CHANNELS[channel]['path'] + r'/art/(?:19|20)\d{2}/art_[0-9a-f]{32}\.html'
    if (parsed.scheme != 'https' or parsed.netloc != HOST or parsed.query or parsed.fragment
            or not re.fullmatch(path, parsed.path)):
        raise ContractError('原件地址不属于所选工信部栏目')
    return value


class _DatedLinks(HTMLParser):
    def __init__(self, channel):
        super().__init__(convert_charrefs=True)
        self.channel = channel
        self.row = None
        self.read_date = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get('class') or '').split()
        if tag == 'li':
            self.row = dict(url='', title='', dates=[]) if 'cf' in classes else None
        elif self.row is not None and tag == 'a' and 'fl' in classes:
            self.row.update(url=attributes.get('href') or '', title=attributes.get('title') or '')
        elif self.row is not None and tag == 'span' and 'fr' in classes:
            self.read_date = True

    def handle_data(self, value):
        if self.read_date and self.row is not None:
            self.row['dates'].append(value)

    def handle_endtag(self, tag):
        if tag == 'span':
            self.read_date = False
        if tag != 'li' or self.row is None:
            return
        row, self.row = self.row, None
        published = ''.join(row['dates']).strip()
        title = ' '.join(row['title'].split())
        if not (0 < len(title) <= 300 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', published)):
            return
        try:
            url = article_address(urljoin(ORIGIN, row['url']), self.channel)
            timepoint(published)
        except (ContractError, ValueError):
            return
        self.links.append((url, title, published))


def dated_links(payload, channel):
    """Read catalogue JSON exactly as received. No script or document fetch here."""
    try:
        response = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=unique_json_object)
        fragment = response['data']['html']
        if response['success'] is not True or response['code'] != '200' or not isinstance(fragment, str):
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ContractError('工信部目录返回结构异常') from error
    page = _DatedLinks(channel)
    page.feed(fragment)
    page.close()
    if not page.links:
        raise ContractError('工信部目录未识别到带日期的已登记原件')
    return tuple(page.links)
