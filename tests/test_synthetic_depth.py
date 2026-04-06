from flint.execution.synthetic_depth import DepthProfile, generate_synthetic_book, VENUE_PROFILES


def test_depth_profile_creation():
    dp = DepthProfile(20_000_000, 20_000_000, 0.7, 0.5)
    assert dp.bid_depth_1pct == 20_000_000
    assert dp.spread_bps == 0.5


def test_generate_synthetic_book():
    dp = DepthProfile(20_000_000, 20_000_000, 0.7, 1.0)
    book = generate_synthetic_book(mid_price=100.0, profile=dp, levels=20)
    assert len(book.bids) == 20
    assert len(book.asks) == 20
    assert book.bids[0].price < 100.0
    assert book.asks[0].price > 100.0
    assert book.bids[0].price > book.bids[-1].price  # descending
    assert book.asks[0].price < book.asks[-1].price  # ascending
    total_bid = sum(l.price * l.size for l in book.bids)
    assert total_bid > 0


def test_spread_applied():
    dp = DepthProfile(10_000_000, 10_000_000, 0.5, 10.0)
    book = generate_synthetic_book(mid_price=100.0, profile=dp, levels=20)
    assert book.bids[0].price < 100.0
    assert book.asks[0].price > 100.0
    spread = book.asks[0].price - book.bids[0].price
    assert spread > 0.05


def test_concentration_affects_distribution():
    low_conc = DepthProfile(10_000_000, 10_000_000, 0.3, 1.0)
    high_conc = DepthProfile(10_000_000, 10_000_000, 0.9, 1.0)
    book_low = generate_synthetic_book(100.0, low_conc, 20)
    book_high = generate_synthetic_book(100.0, high_conc, 20)
    ratio_low = book_low.asks[0].size / book_low.asks[-1].size if book_low.asks[-1].size > 0 else 1
    ratio_high = book_high.asks[0].size / book_high.asks[-1].size if book_high.asks[-1].size > 0 else 1
    assert ratio_high > ratio_low


def test_default_venue_profiles():
    assert "binance" in VENUE_PROFILES
    assert "drift" in VENUE_PROFILES
    assert "hyperliquid" in VENUE_PROFILES
    assert "okx" in VENUE_PROFILES
    assert "bybit" in VENUE_PROFILES
