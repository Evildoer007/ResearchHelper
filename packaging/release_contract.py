"""One ResearchHelper version and candidate identity, independent of OH."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()
if not re.fullmatch(r'\d+\.\d+\.\d+', VERSION):
    raise ValueError('ResearchHelper VERSION must contain one semantic version')
OUTPUT = ROOT / 'result/build-staging/current'
PROTOCOL_ID = 'researchhelper.module-request'
MODULES = ('evidencecollector', 'researchanalyzer', 'thesisbuilder', 'reportcomposer', 'visualrenderer', 'artifactexporter')
