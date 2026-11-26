"""Resolve the issuer and accounting basis before matching a draft amount or rate."""
from collections import defaultdict
import re
from typing import NamedTuple

from .contracts import fingerprint
from .draft_analysis import FINANCIAL, NAV_FIELDS
from .draft_filing_amounts import is_filing_amount, segment_field
from .draft_sources import client_observation_links_enabled
from .evidence import identity_resolutions
from .financial_sources import financial_statement_labels as _statements
from .numeric_integrity import DRAFT_CLAUSE_BOUNDARY, _DRAFT_CODE, _DRAFT_STATEMENT_BOUNDARY, _RETURN_LABEL
from .tinyshare_sources import analysis_field


_TASK_LEAD = re.compile(r'^(?:请帮我|请|帮我|我想)?\s*(?:对比|比较|核对|研究|分析|查看)?\s*')
_SUBJECT_JOIN = re.compile(r'^(?:以及|和|及|与)\s*')
_SUBJECT_BREAK = re.compile(r'[、，,；;。！？\n）)]')
_SUBJECT_NAME = re.compile(r'[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 &.·-]{1,31}')
_POSTSCRIPT = re.compile(r'\s*[（(][^（）()。；\n]{1,64}[）)]')
_CODE_BRACKET = re.compile(r'\s*[（(]\s*')
_AGGREGATE_SCOPE = re.compile(
    r'(?P<sample>(?:样本|样本公司|样本企业)(?:整体|总体|平均|均值|合计|汇总|加权))'
    r'|(?P<population>(?:整个|全部|全)(?:行业|板块|产业链)|'
    r'(?:行业|板块|产业链)(?:整体|总体|平均|均值|合计|汇总|总计|加权))'
)
_UNQUALIFIED_INDUSTRY_SAMPLE = re.compile(r'(?:行业|板块|产业链)(?:的)?样本(?:公司|企业)?(?:的)?(?=[^，,；。]{0,18}$)')
_SCOPE_LIMITATION = re.compile(
    r'(?:不(?:能|可|应)?(?:据此)?(?:代表|推断|认定|等同|视为|外推|代替|替代|等于)|'
    r'未(?:覆盖|代表|取得|提供|形成)|并非|不是|缺少|尚无)'
    r'(?:至|为|的)?[^，,；。！？]{0,12}$|非$'
)
_SCOPE_PREFACE = re.compile(r'(?:看|口径|范围|层面|角度)$')
_INDIVIDUAL_SAMPLE = re.compile(r'(?:该|这家|单家|所选|选定的?|单一)(?:样本|公司|企业|证券|标的)|作为样本')
_RETURN_CONTEXT = re.compile(_RETURN_LABEL)
_NAV_READING = re.compile(r'单位净值|净值读数')
_PRICE_READING = re.compile(r'收盘价|收盘点位|未复权价格|场内价格|股价|价格(?:变化|涨跌幅)|指数点位')
_PRICE_FIELDS = {'open', 'high', 'low', 'close', 'pct_chg'}


class FinancialQualifier(NamedTuple):
    code: str | None = None
    statement: str = ''
    ambiguous: bool = False
    aggregate: str = ''


def has_financial_scope(field):
    return field in FINANCIAL or segment_field(field)


def _aggregate_scope(text):
    matches = [match for match in _AGGREGATE_SCOPE.finditer(text)
               if not _SCOPE_LIMITATION.search(text[:match.start()])]
    return matches[-1].lastgroup if matches else ''


class DraftFinancialScope:
    def __init__(self, task, rows):
        from .publication_content import requested_financial_statement

        aliases = defaultdict(set)
        for row in rows:
            name, code = row.get('object_label'), row.get('code')
            if code and isinstance(name, str) and len(name.strip()) >= 2 and name.upper() != code.upper():
                aliases[name.strip().casefold()].add(code.upper())
        for item in identity_resolutions(task):
            if item['status'] == '代码映射已核验' and item.get('mode') == 'secname':
                aliases[item['query'].casefold()].update(item['codes'])
        # An adjacent name-code pair in the user's question can constrain the
        # draft. It never grants provider identity or financial approval.
        for declaration in (task.get('question', ''), *task.get('objects', [])):
            if not isinstance(declaration, str):
                continue
            previous_end = 0
            for match in _DRAFT_CODE.finditer(declaration):
                left = _SUBJECT_BREAK.split(declaration[previous_end:match.start()])[-1].strip()
                lead = _SUBJECT_JOIN if previous_end else _TASK_LEAD
                name = lead.sub('', left.rstrip(' （(:：'))
                if _SUBJECT_NAME.fullmatch(name):
                    aliases[name.casefold()].add(match.group().upper())
                previous_end = match.end()
        self.aliases = aliases
        self.names = self._name_pattern()
        self.require_named_industry_sample = client_observation_links_enabled(task)

        context = dict(task_id=task.get('id'), research_version=task.get('version'))
        records = defaultdict(list)
        for record in task.get('source_records', []):
            if all(record.get(key) == value for key, value in context.items()):
                records[record.get('id')].append(record)
        self.statements = {}
        for row in rows:
            if not has_financial_scope(analysis_field(row)) or row.get('value') is None:
                continue
            owned = records.get(row.get('artifact_id'), [])
            label = requested_financial_statement(dict(context, source_records=owned), [row])
            if (not label and len(owned) == 1 and owned[0].get('kind') == 'filing' and is_filing_amount(row)
                    and owned[0].get('hash') == row.get('unit_reference', {}).get('snapshot_hash')):
                label = row['locator'].get('statement')
            if not label and len(owned) == 1 and row.get('period_basis') == '来源end_date报告期':
                record = owned[0]
                params = record.get('parameters', {})
                if (record.get('source') == row.get('source') == 'tinyshare'
                        and record.get('operation') == row.get('source_operation')
                        and params.get('ts_code') == row.get('code') and params.get('period') == row.get('period')
                        and params.get('report_type') == '1'
                        and record.get('request_hash') == fingerprint({key: record.get(key)
                            for key in ('source', 'operation', 'parameters')})):
                    label = '合并报表'
            self.statements[row['id']] = label

    def _name_pattern(self):
        aliases = sorted(self.aliases, key=len, reverse=True)
        return re.compile('|'.join(map(re.escape, aliases)), re.I) if aliases else None

    def include_document_issuers(self, disclosures):
        changed = False
        for disclosure in disclosures:
            name, code = disclosure.get('issuer_name'), disclosure.get('issuer_code')
            if name and code and len(name) >= 2 and name.upper() != code.upper():
                codes = self.aliases[name.casefold()]
                if code.upper() not in codes:
                    codes.add(code.upper())
                    changed = True
        if changed:
            self.names = self._name_pattern()

    def _codes(self, text):
        codes = {code.upper() for code in _DRAFT_CODE.findall(text)}
        named = [self.aliases[match.group().casefold()] for match in self.names.finditer(text)] if self.names else []
        # One explicit code can disambiguate a company name shared by A/H
        # listings. It cannot override a contradictory named issuer.
        if len(codes) == 1 and all(codes <= group for group in named):
            return codes
        return codes.union(*named)

    def heading(self, title):
        if not isinstance(title, str):
            return FinancialQualifier()
        if self.names:
            for name in self.names.finditer(title):
                bracket = _CODE_BRACKET.match(title, name.end())
                code = _DRAFT_CODE.match(title, bracket.end()) if bracket else None
                if code and code.group().upper() not in self.aliases[name.group().casefold()]:
                    return FinancialQualifier(ambiguous=True)
        codes, statements = self._codes(title), _statements(title)
        return FinancialQualifier(next(iter(codes)) if len(codes) == 1 else None,
                                  next(iter(statements)) if len(statements) == 1 else '', aggregate=_aggregate_scope(title))

    def read(self, text, start, end, scope, *, phrase_start=0, heading=None):
        before = _DRAFT_STATEMENT_BOUNDARY.split(text[:start])[-1]
        clause = DRAFT_CLAUSE_BOUNDARY.split(before)[-1]
        local = scope.list_context or text[max(start-len(clause), phrase_start):start]
        trailing = _POSTSCRIPT.match(text, end)
        beside = local + (trailing.group() if trailing else '')
        local_codes = self._codes(beside)
        generic_sample = _UNQUALIFIED_INDUSTRY_SAMPLE.search(beside) if self.require_named_industry_sample else None
        if generic_sample:
            # A name-code pair in the question may have included the words
            # “行业样本”. Those words alone do not identify that company.
            local_codes = self._codes(beside[:generic_sample.start()] + '，' + beside[generic_sample.end():])
        prior = before[:-len(local)] if local and not scope.list_context else before
        prior_codes = self._codes(prior)
        codes = local_codes or prior_codes
        explicit_sample = bool(local_codes or _INDIVIDUAL_SAMPLE.search(beside))
        aggregate = _aggregate_scope(beside)
        if (generic_sample and not aggregate and not explicit_sample
                and not _SCOPE_LIMITATION.search(beside[:generic_sample.start()])):
            aggregate = 'sample'
        if not aggregate and not explicit_sample:
            previous = DRAFT_CLAUSE_BOUNDARY.split(prior.rstrip('，, '))[-1]
            if _SCOPE_PREFACE.search(previous):
                aggregate = _aggregate_scope(previous)
        statements = _statements(beside)
        if not statements and (not local_codes or not prior_codes or local_codes == prior_codes):
            statements = _statements(prior)
        if heading and not heading.ambiguous:
            if not explicit_sample and not aggregate:
                aggregate = heading.aggregate
            if not codes and heading.code:
                codes = {heading.code}
            if not statements and heading.statement and (not heading.code or codes == {heading.code}):
                statements = {heading.statement}
        return FinancialQualifier(next(iter(codes)) if len(codes) == 1 else None,
                                  next(iter(statements)) if len(statements) == 1 else '',
                                  len(codes) > 1 or len(statements) > 1 or bool(heading and heading.ambiguous and not codes),
                                  aggregate)

    def logic_exclusions(self, before, after, rows):
        """Record which original rows belong outside the section's stated scope."""
        from .publication_content import _FINANCIAL_NAMES, draft_logic_data_refs

        def fragments(logic):
            argument = logic.get('argument')
            argument = argument if isinstance(argument, dict) else {}
            fields = (logic.get('title'), logic.get('analysis', logic.get('logic')),
                      argument.get('claim'), argument.get('evidence'), argument.get('reasoning'),
                      logic.get('implication'))
            return [part for text in fields if isinstance(text, str)
                    for part in DRAFT_CLAUSE_BOUNDARY.split(text)[::2] if part]

        text, submitted = fragments(after), fragments(before)
        visible = [(self._codes(part), _statements(part)) for part in text]
        original = [(self._codes(part), _statements(part)) for part in submitted]
        codes = set().union(*(subjects for subjects, _ in visible)) or set().union(*(subjects for subjects, _ in original))
        qualifiers = visible if any(statements for _, statements in visible) else original
        shared, specific = set(), defaultdict(set)
        for subjects, statements in qualifiers:
            if not subjects:
                shared.update(statements)
            else:
                for code in subjects:
                    specific[code].update(statements)
        heading = self.heading(after.get('title'))
        measures = {name for names in _FINANCIAL_NAMES.values() for name in names}
        aggregate = heading.aggregate or any(_aggregate_scope(part) and any(name in part for name in measures)
                                              for part in text + submitted)
        samples = set().union(*(self._codes(part) for part in text if not _aggregate_scope(part))) if aggregate else set()
        if aggregate and any(_INDIVIDUAL_SAMPLE.search(part) and not _aggregate_scope(part) for part in text):
            refs = set(draft_logic_data_refs(after))
            selected = {row['code'].upper() for row in rows if row['id'] in refs and row.get('code')
                        and has_financial_scope(analysis_field(row))}
            if len(selected) == 1:
                samples.update(selected)
        return_topic = any(not _SCOPE_LIMITATION.search(part[:match.start()])
                           for part in text + submitted for match in _RETURN_CONTEXT.finditer(part))
        readings = [_RETURN_CONTEXT.sub('', part) for part in text] if return_topic else []
        nav_context = any(_NAV_READING.search(part) for part in readings)
        price_context = any(_PRICE_READING.search(part) for part in readings)
        excluded = []
        for row in rows:
            code = str(row.get('code') or '').upper()
            field = analysis_field(row)
            financial = has_financial_scope(field)
            if ((financial and aggregate and code not in samples)
                    or return_topic and (field in NAV_FIELDS and not nav_context or field in _PRICE_FIELDS and not price_context)):
                excluded.append(row['id'])
                continue
            # Multi-company financial API responses also use cross_section.
            # Only constituent and other non-financial samples are exempt.
            if row.get('axis_role') == 'cross_section' and not financial:
                continue
            if _DRAFT_CODE.fullmatch(code) and (heading.ambiguous or codes and code not in codes):
                excluded.append(row['id'])
                continue
            allowed = shared | specific.get(code, set())
            if allowed and financial and self.statements.get(row['id']) not in allowed:
                excluded.append(row['id'])
        return excluded

    def accepts(self, qualifier, row):
        return (not qualifier.ambiguous and not qualifier.aggregate
                and (not qualifier.code or qualifier.code == str(row.get('code') or '').upper())
                and (not qualifier.statement or qualifier.statement == self.statements.get(row['id'])))

    def unambiguous(self, rows):
        codes = {row['code'].upper() for row in rows if row.get('code')}
        statements = {self.statements[row['id']] for row in rows if self.statements.get(row['id'])}
        return len(codes) <= 1 and len(statements) <= 1


def financial_document_matches(claim, source):
    return (not claim.ambiguous and not source.ambiguous
            and claim.aggregate == source.aggregate
            and (not claim.code or claim.code == source.code)
            and (not claim.statement or claim.statement == source.statement))
