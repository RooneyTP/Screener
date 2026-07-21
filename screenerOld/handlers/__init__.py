"""handlers package — modular command registrations for telegram_bot."""
import logging

logger = logging.getLogger("telegram_bot")

def register_all(app):
    """Register all command handlers from handler submodules."""
    from handlers.trading import register as _reg_trading
    _reg_trading(app)
    from handlers.market import register as _reg_market
    _reg_market(app)
    from handlers.portfolio import register as _reg_portfolio
    _reg_portfolio(app)
    from handlers.admin import register as _reg_admin
    _reg_admin(app)
    logger.info("All command handlers registered from handlers/ package.")
