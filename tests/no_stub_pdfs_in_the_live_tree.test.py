"""No test may leave a fake document in Waleed's real document tree.

On 2026-09-11 he opened an invoice and Acrobat said "damaged and could not be
repaired". Eleven invoices, `INV-MT-TDC-056` to `INV-MT-HDEC-066`, were 14-byte
PDF stubs with no database row. A test suite had monkeypatched the PDF renderer
and then called the REAL `api.save_inv`: its database was isolated, its
DOCUMENTS_ROOT was not, so every run filed a stub in `D:\\PO TOOL\\INVOICES` and
consumed a real invoice number. The Abraj counter and the document list read the
FILESYSTEM, so the app showed eleven invoices nobody could open.

This is the mechanical check that would have caught it on the next run instead
of days later. A real document is hundreds of kilobytes; anything under 200
bytes in these folders is a stub, not a document.

Read-only. It deletes nothing - it names the files and says where they came
from, because the fix is in whichever suite wrote them, not here.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config

# A PDF this small cannot hold a page. The smallest real document in the tree is
# just under 1 MB; the stubs that caused this were 14 bytes.
STUB_BYTES = 200

# Where documents are filed. Taken from config so a moved documents root is
# still checked, and so this test follows the profile the app is set to.
# "LPOs" joined on 2026-09-11. It was missed when this guard was written,
# and a 3-byte PDF and two 133-byte PNGs had been sitting under
# `LPOs/LPO Client A __LPOTEST__/` since August, invisible to it. These are
# the client's OWN documents - a stub here is a counter-signature that
# cannot be opened, which is worse than one of ours.
SUBTREES = ("INVOICES", "QUOTATIONS", "DELIVERY NOTES", "PURCHASE ORDERS", "LPOs")


class NoStubPdfsInTheLiveTree(unittest.TestCase):
    def test_every_filed_pdf_is_a_real_document(self):
        root = Path(config.DOCUMENTS_ROOT)
        if not root.is_dir():
            self.skipTest(f"documents root is not on this machine: {root}")

        stubs = []
        for name in SUBTREES:
            folder = root / name
            if not folder.is_dir():
                continue
            # An LPO can be filed as a scan, so images count here too - a
            # 133-byte PNG is not a purchase order either.
            for pdf in sorted(p for ext in ("*.pdf", "*.png", "*.jpg", "*.jpeg")
                              for p in folder.rglob(ext)):
                try:
                    size = pdf.stat().st_size
                except OSError:
                    continue          # a file that vanished mid-walk is not a stub
                if size <= STUB_BYTES:
                    stubs.append((size, pdf))

        self.assertEqual(
            [], stubs,
            "these are not documents, they are stubs a test left behind - the app "
            "will list them and they cannot be opened:\n"
            + "\n".join(f"    {size:>4} bytes  {path}" for size, path in sorted(stubs))
            + "\n  Find the suite that saved them: it is calling a real save path "
              "with the PDF renderer faked and DOCUMENTS_ROOT still pointing here. "
              "Redirect it to .test-work/<suite>/documents, the way the other "
              "save-path suites do, then delete the files it already wrote.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
