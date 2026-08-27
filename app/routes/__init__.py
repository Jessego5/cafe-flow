from app.routes.barista import router as barista_router
from app.routes.menu import router as menu_router
from app.routes.orders import router as orders_router
from app.routes.slots import router as slots_router

__all__ = ["menu_router", "orders_router", "slots_router", "barista_router"]
