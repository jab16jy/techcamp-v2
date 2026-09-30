"""Every OpenAPI component keeps its plain class name.

When two modules' routers declare response models with the same class name, FastAPI
disambiguates them with module-qualified names (`techcamp__home__...__PlotView`), and
the web's generated `schema.d.ts` loses the plain name the other screens import.
"""

from techcamp.main import app


def test_openapi_component_names_are_not_module_qualified() -> None:
    schemas = app.openapi()["components"]["schemas"]

    qualified = sorted(name for name in schemas if "__" in name)

    assert qualified == []
    assert "PlotView" in schemas
