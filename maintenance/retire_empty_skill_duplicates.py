"""Compatibility entry for the shared, guarded delivery reconciliation."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'packaging'))
from reconcile_delivery import (ROOT,NAME,COPY_NAME,COPY_EXTENSIONS,digest,copy_original,
    manifest_authority,historical_copy_authority,skill_inventory as inventory,
    apply_skill_inventory as apply_inventory)
