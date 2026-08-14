from fastapi import FastAPI

from app.routers import auth, cutting_optimizer, materials, pattern_specs, production, products, reports, sales, settings, stock, suppliers

app = FastAPI(title="Oura Studios API", version="3.8.0")

app.include_router(auth.router, prefix="/api/v1")
app.include_router(materials.router, prefix="/api/v1")
app.include_router(suppliers.router, prefix="/api/v1")
app.include_router(products.router, prefix="/api/v1")
app.include_router(stock.router, prefix="/api/v1")
app.include_router(pattern_specs.router, prefix="/api/v1")
app.include_router(cutting_optimizer.router, prefix="/api/v1")
app.include_router(production.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(sales.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
