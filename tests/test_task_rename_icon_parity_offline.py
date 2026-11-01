"""Independent OH rename-icon contract. Archive remains an RH operation."""
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path('/Users/haoranxu/Desktop/OptionHelper/products/app/frontend/shared/app.js')


def reference_rename_paths():
    source = REFERENCE.read_text()
    match = re.search(r'const editIcon = \(\) => createIcon\((\[[\s\S]*?\])\);', source)
    if match is None:
        raise ValueError('OH rename icon declaration is missing or changed')
    return json.loads(re.sub(r',\s*\]', ']', match.group(1)))


class TaskRenameIconParityTests(unittest.TestCase):
    def test_rename_paths_are_exact_oh_geometry(self):
        source = (ROOT / 'products/app/frontend/ui/task-menu.js').read_text()
        match = re.search(r"edit:'([^']+)'", source)
        self.assertIsNotNone(match)
        icon = ET.fromstring('<svg>' + match.group(1) + '</svg>')
        self.assertEqual([element.attrib['d'] for element in icon], reference_rename_paths())

    def test_menu_keeps_oh_size_and_foreground_fill(self):
        source = (ROOT / 'products/app/frontend/ui/task-menu.js').read_text()
        self.assertIn('viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"', source)

    def test_archive_is_not_replaced_by_oh_delete(self):
        source = (ROOT / 'products/app/frontend/ui/task-menu.js').read_text()
        self.assertIn("archive:'<path", source)
        self.assertNotIn("trash:'", source)


if __name__ == '__main__':
    unittest.main()
