"""Data models, enums, exception classes, and mathematical invariants
for the Wallex Market Ingestion & Client Subsystem.
Strictly relies on Python dataclasses and pure NumPy/math primitives (NO pandas).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


# ==============================================================================
# 1. Enums
# ==============================================================================

class OrderSide(str, Enum):
    """Order transaction side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order routing execution type."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    """Lifecycle execution status of an exchange order."""
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ==============================================================================
# 2. Exceptions Hierarchy
# ==============================================================================

class WallexClientError(Exception):
    """Base exception for all Wallex client errors."""
    def __init__(self, message: str, code: Optional[Union[str, int]] = None, raw_response: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.raw_response = raw_response


class WallexRateLimitError(WallexClientError):
    """Raised when request rate limit is exceeded (HTTP 429)."""
    pass


class WallexAuthError(WallexClientError):
    """Raised on authentication or permission failure (HTTP 401/403)."""
    pass


class WallexNetworkError(WallexClientError):
    """Raised on connection timeout, socket drop, or transport failure."""
    pass


class WallexOrderError(WallexClientError):
    """Raised on order validation or exchange placement error."""
    pass


class WallexInsufficientFundsError(WallexOrderError):
    """Raised when available balance is insufficient for order."""
    pass


class WallexAPIError(WallexClientError):
    """Raised when exchange returns an unhandled JSON-RPC or REST API error."""
    pass


class CorruptMarketDataError(WallexClientError):
    """Raised when orderbook or ticker feed is corrupted or crossed."""
    pass


# ==============================================================================
# 3. Market Ingestion & Order Book Models
# ==============================================================================

@dataclass(frozen=True)
class DepthLevel:
    """A single price and quantity level in the orderbook."""
    price: float
    quantity: float

    def __getitem__(self, item: int) -> float:
        """Allow tuple-like access (level[0] = price, level[1] = quantity)."""
        if item == 0:
            return self.price
        elif item == 1:
            return self.quantity
        raise IndexError("DepthLevel index out of range (0=price, 1=quantity)")


@dataclass(frozen=True)
class Candle:
    """Historical OHLCV candle record."""
    timestamp: float                         # Milliseconds epoch
    open: float
    high: float
    low: float
    close: float
    volume: float
    resolution: str = "15"                   # Timeframe ('1', '5', '15', '60')


@dataclass(frozen=True)
class OrderBook:
    """Top-10 depth orderbook with bid/ask volume, spread, and imbalance calculations."""
    symbol: str
    timestamp: float                         # Milliseconds epoch
    bids: List[Tuple[float, float]]          # Top bids [(price, volume), ...] sorted descending
    asks: List[Tuple[float, float]]          # Top asks [(price, volume), ...] sorted ascending

    @property
    def timestamp_ms(self) -> float:
        """Unix epoch in milliseconds, auto-normalizing from seconds if < 1e11."""
        if self.timestamp <= 0:
            return 0.0
        return self.timestamp * 1000.0 if self.timestamp < 1e11 else float(self.timestamp)

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return self.best_bid or self.best_ask

    @property
    def spread(self) -> float:
        if self.best_bid is not None and self.best_ask is not None:
            return max(0.0, self.best_ask - self.best_bid)
        return 0.0

    @property
    def spread_pct(self) -> float:
        """Percentage spread: (best_ask - best_bid) / best_bid."""
        if self.best_bid is not None and self.best_ask is not None and self.best_bid > 0:
            return (self.best_ask - self.best_bid) / self.best_bid
        return 0.0

    def total_bid_volume(self, depth: int = 10) -> float:
        return sum(vol for _, vol in self.bids[:depth])

    def total_ask_volume(self, depth: int = 10) -> float:
        return sum(vol for _, vol in self.asks[:depth])

    def calculate_imbalance(self, depth: int = 10) -> float:
        """
        Calculates Order Book Imbalance (OBI):
        OBI = (BidVol - AskVol) / (BidVol + AskVol) in [-1.0, +1.0].
        Returns 0.0 if total volume is zero.
        """
        bid_vol = self.total_bid_volume(depth)
        ask_vol = self.total_ask_volume(depth)
        total_vol = bid_vol + ask_vol
        if total_vol <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, (bid_vol - ask_vol) / total_vol))

    def is_stale(self, max_age_ms: float = 1500.0, current_time_ms: Optional[float] = None) -> bool:
        """Flags orderbook as stale if age exceeds max_age_ms."""
        if current_time_ms is not None:
            now_ms = current_time_ms * 1000.0 if (0 < current_time_ms < 1e11) else float(current_time_ms)
        else:
            now_ms = time.time() * 1000.0
        return (now_ms - self.timestamp_ms) > max_age_ms


@dataclass(frozen=True)
class MarketDataSnapshot:
    """Primary market data contract emitted by src/wallex for Strategy and Risk modules."""
    symbol: str                              # e.g., "BTCTMN", "ETHTMN"
    timestamp: float                         # Unix epoch in milliseconds
    best_bid: float
    best_ask: float
    spread_pct: float                        # (best_ask - best_bid) / best_bid
    bids: List[Tuple[float, float]]          # Top 10 [(price, volume), ...]
    asks: List[Tuple[float, float]]          # Top 10 [(price, volume), ...]
    imbalance_ratio: float                   # (BidVol - AskVol) / (BidVol + AskVol)
    recent_candles: List[Dict[str, Any]] = field(default_factory=list)
    is_stale: bool = False

    def __post_init__(self) -> None:
        """Enforce Unix epoch in milliseconds contract invariant."""
        if 0 < self.timestamp < 1e11:
            object.__setattr__(self, "timestamp", float(self.timestamp * 1000.0))

    @classmethod
    def from_orderbook(
        cls,
        orderbook: OrderBook,
        recent_candles: Optional[List[Dict[str, Any]]] = None,
        max_age_ms: float = 1500.0,
        current_time_ms: Optional[float] = None,
    ) -> MarketDataSnapshot:
        """Factory creating MarketDataSnapshot from an OrderBook instance."""
        best_bid = orderbook.best_bid or 0.0
        best_ask = orderbook.best_ask or 0.0
        spread_pct = orderbook.spread_pct
        imbalance = orderbook.calculate_imbalance(depth=10)
        is_stale = orderbook.is_stale(max_age_ms, current_time_ms)

        # Crossed book sanity check: best_bid >= best_ask with both > 0
        if best_bid > 0 and best_ask > 0 and best_bid > best_ask:
            is_stale = True

        return cls(
            symbol=orderbook.symbol,
            timestamp=orderbook.timestamp_ms,
            best_bid=best_bid,
            best_ask=best_ask,
            spread_pct=spread_pct,
            bids=orderbook.bids[:10],
            asks=orderbook.asks[:10],
            imbalance_ratio=imbalance,
            recent_candles=recent_candles or [],
            is_stale=is_stale,
        )


# ==============================================================================
# 4. Account & Ledger Models
# ==============================================================================

@dataclass(frozen=True)
class AccountBalance:
    """Spot credit sub-account balance ledger."""
    toman_available: float                   # Available TMN (e.g. 5,000,000)
    toman_locked: float                      # TMN locked in active open orders
    crypto_balances: Dict[str, float]        # e.g. {"BTC": 0.00025, "ETH": 0.0}
    total_equity_tmn: float                  # Mark-to-market equity in TMN
    timestamp: float                         # Epoch timestamp (seconds or ms)

    @property
    def total_toman(self) -> float:
        return self.toman_available + self.toman_locked

    def is_all_toman(self, dust_threshold: float = 1e-6) -> bool:
        """Returns True if portfolio is 100% liquidated to Toman (pre-deadline check)."""
        return all(qty <= dust_threshold for qty in self.crypto_balances.values())

    def get_crypto(self, symbol: str) -> float:
        """Safe retrieval of crypto asset balance defaulting to 0.0."""
        clean_sym = symbol.replace("TMN", "").replace("_TMN", "").upper()
        return self.crypto_balances.get(clean_sym, 0.0)


# ==============================================================================
# 5. Order Request & Execution Models
# ==============================================================================

@dataclass
class OrderRequest:
    """Internal order request routed to Wallex client."""
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: float
    quantity: float
    stop_price: Optional[float] = None


@dataclass
class OrderExecutionResult:
    """Exchange response and fill execution details."""
    client_order_id: str
    exchange_order_id: Optional[str]
    symbol: str
    status: OrderStatus
    filled_quantity: float
    average_fill_price: float
    fee_paid_tmn: float
    executed_at: float
    raw_response: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# 6. Wallex Credit Market Inactive Whitelist / Blacklist & Normalization
# ==============================================================================

def normalize_symbol(symbol: str) -> str:
    """Normalizes exchange symbol to uppercase alphanumeric (e.g. 'BTC/TMN' -> 'BTCTMN')."""
    if not symbol:
        return ""
    return symbol.upper().replace("/", "").replace("-", "").replace("_", "").strip()


# Official list of Wallex markets disabled for credit sub-accounts.
# Source: Wallex Exchange Credit Facility Notice (November 2024 / StarCoach Hackathon).
WALLEX_CREDIT_DISABLED_SYMBOLS: frozenset[str] = frozenset({
    "ATMN", "GTMN", "TTMN", "WTMN", "XTMN", "2ZTMN", "AUSDT", "GUSDT", "IOTMN", "OPTMN",
    "TUSDT", "WUSDT", "XUSDT", "2ZUSDT", "AMPTMN", "APTTMN", "ATHTMN", "BATTMN", "BIOTMN",
    "C98TMN", "CATTMN", "CHRTMN", "CRVTMN", "CVCTMN", "CVXTMN", "EDUTMN", "ENSTMN", "FETTMN",
    "FLRTMN", "GLMTMN", "GMTTMN", "GNOTMN", "GRTTMN", "HOTTMN", "ICPTMN", "ILVTMN", "INJTMN",
    "IOUSDT", "JSTTMN", "JTOTMN", "JUPTMN", "KASTMN", "KCSTMN", "KNCTMN", "LPTTMN", "LRCTMN",
    "MDTTMN", "METTMN", "MMTTMN", "MNTTMN", "MTLTMN", "NFTTMN", "NMRTMN", "NOTTMN", "OMGTMN",
    "ONETMN", "OPUSDT", "PYRTMN", "QNTTMN", "RAYTMN", "REQTMN", "RLCTMN", "RPLTMN", "RSRTMN",
    "SFPTMN", "SKLTMN", "SKYTMN", "SLPTMN", "SNXTMN", "SSVTMN", "TAOTMN", "TIATMN", "TONTMN",
    "TRBTMN", "TWTTMN", "UMATMN", "WIFTMN", "WINTMN", "WOOTMN", "XMRTMN", "XPLTMN", "XTZTMN",
    "YGGTMN", "ZECTMN", "ZENTMN", "ZILTMN", "ZROTMN", "ZRXTMN", "AEROTMN", "AEVOTMN", "AIOZTMN",
    "ALGOTMN", "AMPUSDT", "ANKRTMN", "API3TMN", "APTUSDT", "ARKMTMN", "ATHUSDT", "AVNTTMN",
    "BANDTMN", "BANKTMN", "BATUSDT", "BICOTMN", "BIOUSDT", "BLURTMN", "BOMETMN", "BONKTMN",
    "BTTCTMN", "C98USDT", "CATITMN", "CATUSDT", "CELRTMN", "CGPTTMN", "CHRUSDT", "COMPTMN",
    "CRVUSDT", "CTSITMN", "CVCUSDT", "CVXUSDT", "DASHTMN", "DEXETMN", "DOGSTMN", "EDUUSDT",
    "ENSUSDT", "FETUSDT", "FLOWTMN", "FLRUSDT", "FRAXTMN", "GLMUSDT", "GMTUSDT", "GNOUSDT",
    "GRAMTMN", "GRTUSDT", "HOTUSDT", "ICPUSDT", "ILVUSDT", "INJUSDT", "JSTUSDT", "JTOUSDT",
    "JUPUSDT", "KAIATMN", "KASUSDT", "KCSUSDT", "KITETMN", "KMNOTMN", "KNCUSDT", "LPTUSDT",
    "LRCUSDT", "MASKTMN", "MDTUSDT", "MEMETMN", "METUSDT", "MMTUSDT", "MNTUSDT", "MOVETMN",
    "MTLUSDT", "NEARTMN", "NEXOTMN", "NFTUSDT", "NMRUSDT", "NOTUSDT", "OMGUSDT", "ONDOTMN",
    "ONEUSDT", "ORAITMN", "ORCATMN", "OSMOTMN", "PAXGTMN", "PNUTTMN", "POLSTMN", "PYRUSDT",
    "PYTHTMN", "QNTUSDT", "RAYUSDT", "REQUSDT", "RLCUSDT", "RPLUSDT", "RSRUSDT", "RUNETMN",
    "SAFETMN", "SFPUSDT", "SKLUSDT", "SKYUSDT", "SLPUSDT", "SNXUSDT", "SOONTMN", "SSVUSDT",
    "STRKTMN", "TAOUSDT", "TIAUSDT", "TONUSDT", "TRACTMN", "TRBUSDT", "TWTTMN", "TWTUSDT",
    "UMAUSDT", "USDSTMN", "WBTCTMN", "WIFUSDT", "WINUSDT", "WOOUSDT", "XMRUSDT", "XPLUSDT",
    "XTZUSDT", "YFIUSDT", "YGGUSDT", "ZECUSDT", "ZENUSDT", "ZETATMN", "ZILUSDT", "ZORATMN",
    "ZROUSDT", "ZRXUSDT", "AAPLXTMN", "AEROUSDT", "AEVOUSDT", "AIOZUSDT", "AIXBTTMN", "ALGOUSDT",
    "AMZNXTMN", "ANKRUSDT", "API3USDT", "ARKMUSDT", "ASTERTMN", "AUDIOTMN", "AVNTUSDT", "BANDUSDT",
    "BANKUSDT", "BICOUSDT", "BLURUSDT", "BOMEUSDT", "BONKUSDT", "BTTCUSDT", "CATIUSDT", "CELRUSDT",
    "CGPTUSDT", "COINXTMN", "COMPUSDT", "CTSIUSDT", "CYBERTMN", "DASHUSDT", "DEXEUSDT", "DOGSUSDT",
    "ETHFITMN", "FLOWUSDT", "FRAXUSDT", "GRAMUSDT", "HMSTRTMN", "HOODXTMN", "JASMYTMN", "KAIAUSDT",
    "KAITOTMN", "KITEUSDT", "KMNOUSDT", "LAYERTMN", "LINEATMN", "MAGICTMN", "MAJORTMN", "MASKUSDT",
    "MEMEUSDT", "METAXTMN", "METISTMN", "MOVEUSDT", "NEARUSDT", "NEIROTMN", "NEXOUSDT", "NVDAXTMN",
    "ONDOUSDT", "ORAIUSDT", "ORCAUSDT", "OSMOUSDT", "PAXGUSDT", "PIXELTMN", "PNUTUSDT", "POLSUSDT",
    "PROVETMN", "PYTHUSDT", "RUNEUSDT", "SAFEUSDT", "SENSOTMN", "SOONUSDT", "STRKUSDT", "SUPERTMN",
    "SYRUPTMN", "TOSHITMN", "TRACUSDT", "TSLAXTMN", "TURBOTMN", "UNGONTMN", "USDSUSDT", "WAVESTMN",
    "WBTCUSDT", "ZETAUSDT", "ZORAUSDT", "AAPLXUSDT", "AIXBTUSDT", "AMZNXUSDT", "ASTERUSDT",
    "AUDIOUSDT", "BANANATMN", "COINXUSDT", "CYBERUSDT", "EIGENUSDT", "ETHFIUSDT", "GIGGLETMN",
    "HMSTRUSDT", "HOODXUSDT", "JASMYUSDT", "KAITOUSDT", "LAYERUSDT", "LINEAUSDT", "MAGICUSDT",
    "MAJORUSDT", "MEMEFITMN", "METAXUSDT", "METISUSDT", "MORPHOTMN", "NEIROUSDT", "NVDAXUSDT",
    "PENDLETMN", "PIXELUSDT", "PPLTONTMN", "PROVEUSDT", "RENDERTMN", "SAHARATMN", "SENSOUSDT",
    "SUPERUSDT", "SYRUPUSDT", "TOSHIUSDT", "TSLAXUSDT", "TURBOUSDT", "UNGONUSDT", "WAVESUSDT",
    "BANANAUSDT", "GIGGLEUSDT", "MEMEFIUSDT", "MORPHOUSDT", "PENDLEUSDT", "PEOPLEUSDT",
    "PPLTONUSDT", "RENDERUSDT", "SAHARAUSDT", "VIRTUALTMN", "VIRTUALUSDT", "1BBABYDOGETMN",
    "1BBABYDOGEUSDT"
})


def is_credit_market_supported(symbol: str) -> bool:
    """Returns True if the symbol is permitted for trading in the Wallex credit facility.
    Liquid core pairs like BTCTMN, ETHTMN, USDTTMN, SOLTMN, DOGETMN, XRPTMN return True.
    """
    norm = normalize_symbol(symbol)
    if not norm:
        return False
    return norm not in WALLEX_CREDIT_DISABLED_SYMBOLS


def validate_credit_market_symbol(symbol: str) -> None:
    """Raises WallexOrderError if the symbol is blacklisted or invalid for credit trading."""
    norm = normalize_symbol(symbol)
    if not norm:
        raise WallexOrderError("Invalid order symbol: empty or missing symbol string.")
    if norm in WALLEX_CREDIT_DISABLED_SYMBOLS:
        raise WallexOrderError(
            f"Symbol '{symbol}' ({norm}) is disabled for Wallex credit facility trading. "
            "Please select an authorized liquid pair (e.g., BTCTMN, ETHTMN, USDTTMN, SOLTMN)."
        )

