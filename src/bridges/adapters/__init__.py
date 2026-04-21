"""Bridge adapters for normalized bridge registry."""

from .generic_adapter import ExistingBridgeAdapter
from .sec_filing_adapter import SECFilingAdapter
from .fed_board_adapter import FedBoardAdapter
from .bls_adapter import BLSAdapter
from .aviation_adapter import AviationAdapter
from .whitehouse_adapter import WhitehouseAdapter
from .treasury_ofac_adapter import TreasuryOfacAdapter
from .cftc_adapter import CFTCAdapter
from .noaa_adapter import NOAAAdapter

__all__ = [
    "ExistingBridgeAdapter",
    "SECFilingAdapter",
    "FedBoardAdapter",
    "BLSAdapter",
    "AviationAdapter",
    "WhitehouseAdapter",
    "TreasuryOfacAdapter",
    "CFTCAdapter",
    "NOAAAdapter",
]
