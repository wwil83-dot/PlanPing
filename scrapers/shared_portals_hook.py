"""
Installs the shared-portal filter without editing idox_scraper.py's logic
(2026-10-08).

Importing this module wraps IdoxPortal.scrape so that, for a council on a
shared portal (see shared_portals.py), the applications it returns are cut down
to those whose postcode is in THAT council's own district before anything is
geocoded or saved. If the postcode lookup fails, the wrapper returns nothing, so
tonight's run saves nothing for that council rather than the unfiltered list.
Councils not on a shared portal are untouched.

Use: add ONE line to the bottom of idox_scraper.py, inside the final
`if __name__ == "__main__":` block, before asyncio.run(main()):
        import shared_portals_hook
"""
import sys

from shared_portals import COUNCIL_DISTRICT, filter_to_council


def wrap(portal_cls) -> bool:
    """Wrap portal_cls.scrape once. Returns False if it was already wrapped."""
    if getattr(portal_cls.scrape, "_shared_portal_filter", False):
        return False
    original = portal_cls.scrape

    async def scrape(self, *args, **kwargs):
        apps = await original(self, *args, **kwargs)
        if self.council_name in COUNCIL_DISTRICT and apps:
            try:
                apps, stats = await filter_to_council(apps, self.council_name)
            except Exception as e:                              # fail closed
                print(f"    [{self.council_name}] ✗ shared-portal filter failed — "
                      f"saving nothing tonight, never the unfiltered list: {type(e).__name__}: {str(e)[:120]}")
                return []
            print(f"    [{self.council_name}] shared portal: kept {stats['kept']} in its own district, "
                  f"{stats['other_council']} belong to the other council, "
                  f"{stats['unresolved']} have no usable postcode (not saved)")
        return apps

    scrape._shared_portal_filter = True
    portal_cls.scrape = scrape
    return True


def install() -> int:
    """Patch IdoxPortal wherever it lives (the script runs as __main__)."""
    done = 0
    for name in ("__main__", "idox_scraper"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "IdoxPortal"):
            done += wrap(mod.IdoxPortal)
    return done


install()
