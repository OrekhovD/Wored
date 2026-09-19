from app import app


async def test_trader_route_redirects_to_the_common_trading_day_surface() -> None:
    route = next(route for route in app.router.routes if getattr(route, "path", None) == "/trader")

    response = await route.endpoint()

    assert response.status_code == 307
    assert response.headers["location"] == "/trading-day"
