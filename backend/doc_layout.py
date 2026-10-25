# -*- coding: utf-8 -*-
"""The page-area model of docs/12, in ONE place (2026-09-05).

Why this exists
---------------
`docs/12-PDF-LAYOUT-CONSTITUTION.md` §2 describes four page ROLES and the area
each one reserves. That model was implemented three times - once in
`quote_generator`, once in `dn_generator`, once in `inv_generator` - as four
identical arithmetic lines and an identical role-selection branch.

Three copies of one model is three places to forget something, and on
2026-09-05 all three had forgotten the same thing: the role selection handled
`header`, `body` and `footer` pages but not the **single** page. When the
greedy packer happened to fit every row into one chunk, that page was handed
`footer_area` - the HEADERLESS area - while still rendering the header. A DN of
19 wrapping rows built a 234mm table under a 75mm header on a 273mm page and
printed on two sheets under a footer that read "Page 1 of 1". Quote and invoice
carried the same defect; their single-page guard just caught the cases first.

So this module owns the part that is genuinely the same for every type: the
four areas, the four capacities, and which one a given page gets. It does NOT
own packing, row stretching or last-page trimming - those differ per type by
measurement, and docs/12 §6 is explicit that a constant must never be copied
between types without re-measuring.

Nothing here reads config or global state. It is arithmetic over numbers the
caller has already resolved, which is what makes it testable without rendering.
"""
from __future__ import annotations

from dataclasses import dataclass


class LayoutError(ValueError):
    """Geometry that cannot produce a printable page."""


#: The four page roles of docs/12 §2.
HEADER_PAGE = "header"   # page 1 of a multi-page doc: header block + page number
BODY_PAGE = "body"       # middle pages: tiny top margin + page number (the biggest)
FOOTER_PAGE = "footer"   # last page of a multi-page doc: tiny top margin + closing
SINGLE_PAGE = "single"   # the ONLY page: header block AND closing

ROLES = (HEADER_PAGE, BODY_PAGE, FOOTER_PAGE, SINGLE_PAGE)


def role_for(page_index: int, total_pages: int) -> str:
    """Which role page `page_index` (0-based) of `total_pages` plays.

    The whole point of this function is that `single` is not a special case of
    `footer`. A one-page document renders the header AND the closing block, so
    it reserves both; treating it as a footer page hands it the headerless area
    and the table then runs off the bottom of the sheet.
    """
    if total_pages <= 1:
        return SINGLE_PAGE
    if page_index == 0:
        return HEADER_PAGE
    if page_index == total_pages - 1:
        return FOOTER_PAGE
    return BODY_PAGE


@dataclass(frozen=True)
class PageGeometry:
    """One document type's measured millimetres, inside the @page margins.

    Every field is a reserve the RENDERED page actually spends. Where a value
    is also drawn by the print CSS it must reach the stylesheet as a CSS
    variable rather than being written there a second time - docs/12 §4.7, and
    `tests/layout_css_engine_coupling.test.py`.
    """

    usable_mm: float          #: content-box height (*_USABLE_MM)
    top_mm: float             #: header block, page 1 / single only (*_TOP_MM)
    cont_top_mm: float        #: headerless top margin (*_CONT_TOP_MM)
    head_row_mm: float        #: the table's own <thead> (*_HEADER_ROW_MM)
    pagenum_mm: float         #: page-number band (*_PAGENUM_MM)
    last_reserve_mm: float    #: closing + footer/signature + safe gap
    #: rows the engine reserves INSIDE the last page's table (grand total, or
    #: tax + total). Zero where the totals render outside the table.
    last_fixed_in_table_mm: float = 0.0
    #: per-skin page chrome the type-tuned constants never modelled
    #: (*_ESH_PAGE_CHROME_MM); 0 for the default skin - docs/12 §6.1.
    chrome_mm: float = 0.0
    #: safety margin subtracted from a page's packing capacity
    safety_mm: float = 0.0

    def __post_init__(self):
        for name in ("usable_mm", "top_mm", "cont_top_mm", "head_row_mm",
                     "pagenum_mm", "last_reserve_mm", "last_fixed_in_table_mm",
                     "chrome_mm", "safety_mm"):
            if float(getattr(self, name)) < 0:
                raise LayoutError(f"{name} is negative")

    # ---------------------------------------------------------------- areas
    # docs/12 §2. Each role reserves ONLY what it actually renders - the
    # "golden rule" is that a headerless page never reserves the header block.
    @property
    def header_area(self) -> float:
        return self.usable_mm - self.top_mm - self.pagenum_mm - self.chrome_mm

    @property
    def body_area(self) -> float:
        return self.usable_mm - self.cont_top_mm - self.pagenum_mm - self.chrome_mm

    @property
    def footer_area(self) -> float:
        return self.usable_mm - self.cont_top_mm - self.last_reserve_mm - self.chrome_mm

    @property
    def single_area(self) -> float:
        return self.usable_mm - self.top_mm - self.last_reserve_mm - self.chrome_mm

    def area(self, role: str) -> float:
        try:
            return {HEADER_PAGE: self.header_area, BODY_PAGE: self.body_area,
                    FOOTER_PAGE: self.footer_area, SINGLE_PAGE: self.single_area}[role]
        except KeyError:
            raise LayoutError(f"unknown page role {role!r}") from None

    def area_for(self, page_index: int, total_pages: int) -> float:
        """The area for this page - the call that used to be a four-branch `if`
        copied into three generators, with the `single` branch missing."""
        return self.area(role_for(page_index, total_pages))

    # ------------------------------------------------------------ capacities
    def capacity(self, role: str) -> float:
        """How much ROW height this page can hold: its area less the thead, the
        last page's in-table totals, and the safety margin.

        The single page deliberately takes no safety margin - it already
        reserves the whole closing block, and docs/12 §4.3 forbids it filling.
        """
        area = self.area(role)
        fixed = self.last_fixed_in_table_mm if role in (FOOTER_PAGE, SINGLE_PAGE) else 0.0
        safety = 0.0 if role == SINGLE_PAGE else self.safety_mm
        return area - self.head_row_mm - fixed - safety

    def capacity_for(self, page_index: int, total_pages: int) -> float:
        return self.capacity(role_for(page_index, total_pages))

    # ------------------------------------------------------------- invariants
    def problems(self) -> list[str]:
        """The §4 anti-overlap invariants, as checks rather than prose.

        Returned rather than raised: a generator should not explode mid-save on
        a geometry that merely looks tight. `tests/` assert this is empty; a
        caller that wants hard enforcement can raise on it.
        """
        out: list[str] = []
        for role in ROLES:
            if self.area(role) <= 0:
                out.append(f"{role} area is {self.area(role):.1f}mm - nothing can render")
            elif self.capacity(role) <= 0:
                out.append(f"{role} page has {self.capacity(role):.1f}mm for rows "
                           "once the thead and totals are reserved")
        # The GOLDEN RULE (docs/12 §2): a headerless page never reserves the
        # header block. Stated as a comparison of the two top reserves, because
        # that is where it is actually decided - comparing the resulting AREAS
        # cannot see the breach when the two reserves are equal, which is
        # exactly the shape the Phase X.27 bug had.
        if self.top_mm > 0 and self.cont_top_mm >= self.top_mm:
            out.append(
                f"cont_top_mm ({self.cont_top_mm}) is not smaller than top_mm "
                f"({self.top_mm}) - a headerless page is reserving as much as "
                "page 1, so every middle page wastes the header band (docs/12 §2)")
        # A single page reserves both the header and the closing block, so it
        # is necessarily the smallest. If it is not, one of them is missing.
        if self.single_area > self.header_area or self.single_area > self.footer_area:
            out.append("single_area is not the smallest area - a single page "
                       "must reserve BOTH the header and the closing block")
        return out

    def validate(self) -> "PageGeometry":
        problems = self.problems()
        if problems:
            raise LayoutError("; ".join(problems))
        return self


def fits_on_one_page(geometry: PageGeometry, heights: list[float]) -> bool:
    """Whether these rows fit the SINGLE-page capacity.

    Named because getting it wrong is subtle: the question is not "do they fit
    the biggest page" but "do they fit the page that also carries the header and
    the closing block", which is the smallest of the four.
    """
    return not heights or sum(heights) <= geometry.capacity(SINGLE_PAGE)
