import sys
import os

# Set python path to backend root so we can import app modules
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from app.database import SessionLocal
from app.models.product import Product, ProductSize
from app.routers.products import generate_shopee_bulk_upload
import openpyxl

def main():
    print("Verifying Shopee Bulk Upload Excel Generator...")
    db = SessionLocal()
    
    # Start a transaction so all modifications are rolled back at the end
    db.begin()
    
    try:
        # Find an existing product to temporarily modify
        test_product = db.query(Product).filter(Product.is_archived.is_(False)).first()
        if not test_product:
            print("❌ Error: No products found in the database to run the test.")
            return
            
        print(f"Temporarily setting product {test_product.sku} ({test_product.name}) to category 'scrunchie'...")
        test_product.category = "scrunchie"
        
        # Ensure it has at least one active size with a selling price > 0
        test_sizes = db.query(ProductSize).filter(
            ProductSize.product_id == test_product.id,
            ProductSize.is_archived.is_(False)
        ).all()
        
        print(f"Found {len(test_sizes)} existing sizes for {test_product.sku}.")
        # Set all of them to be active and priced
        for s in test_sizes:
            s.selling_price = 25000.0
            s.is_archived = False
            
        db.flush()
            
        # Run generator
        excel_stream = generate_shopee_bulk_upload(db)
        wb = openpyxl.load_workbook(excel_stream)
        
        # Check sheet name
        sheet_name = "Template"
        print(f"First sheet name: {sheet_name}")
        ws = wb[sheet_name]
        
        # Check total rows and columns
        max_row = ws.max_row
        max_col = ws.max_column
        print(f"Sheet dimensions: {max_row} rows x {max_col} columns")
        
        # Verify headers (Rows 1-6)
        print("Verifying rows 1-6 headers...")
        row2_vals = [ws.cell(row=2, column=c).value for c in range(1, 5)]
        print(f"Row 2 token: {row2_vals}")
        assert "basic" in str(row2_vals[0]), "Token 'basic' not found in row 2 col 1!"
        assert "007c3be4a959861bbde1d1ea24b6e962" in str(row2_vals[1]), "Template hash token not found in row 2 col 2!"
        print("✅ Header check passed!")
        
        # Print and verify all data rows
        print(f"\n--- Data Rows (Row 7 to {max_row}) ---")
        current_product_sku = None
        current_product_name = None
        
        for r in range(7, max_row + 1):
            row_vals = [ws.cell(row=r, column=c).value for c in range(1, 42)]
            print(f"Row {r} Data:")
            print(f" - ps_category: {row_vals[0]}")
            print(f" - ps_product_name: {row_vals[1]!r}")
            desc_val = (row_vals[2][:50] + "...") if row_vals[2] else "None"
            print(f" - ps_product_description: {desc_val!r}")
            print(f" - ps_sku_parent_short: {row_vals[8]!r}")
            print(f" - et_title_variation_integration_no: {row_vals[10]!r}")
            print(f" - et_title_variation_1: {row_vals[11]!r}")
            print(f" - et_title_option_for_variation_1: {row_vals[12]!r}")
            print(f" - ps_price: {row_vals[16]}")
            print(f" - ps_stock: {row_vals[17]}")
            print(f" - ps_sku_short: {row_vals[18]!r}")
            print(f" - ps_weight: {row_vals[31]}")
            print(f" - L, W, H: {row_vals[32]}, {row_vals[33]}, {row_vals[34]}")
            print(f" - Logis (8003): {row_vals[36]}")
            
            # Assert category and corresponding weight/dimensions
            cat_code = row_vals[0]
            assert cat_code in ("100146", "101650"), f"Incorrect category code: {cat_code}"
            
            if cat_code == "100146":
                assert row_vals[31] == 50, f"Incorrect weight for scrunchie: {row_vals[31]}"
                assert row_vals[32] == 10 and row_vals[33] == 10 and row_vals[34] == 2, f"Incorrect dimensions for scrunchie"
            elif cat_code == "101650":
                assert row_vals[31] == 100, f"Incorrect weight for pouch: {row_vals[31]}"
                assert row_vals[32] == 15 and row_vals[33] == 12 and row_vals[34] == 5, f"Incorrect dimensions for pouch"
            
            assert row_vals[11] == "Ukuran", f"Incorrect variation name: {row_vals[11]}"
            assert row_vals[35] == "Nonaktif", "Same Day should be Nonaktif"
            assert row_vals[36] == "Aktif", "Reguler Cashless should be Aktif"
            assert row_vals[37] == "Nonaktif", "Hemat Kargo should be Nonaktif"
            assert row_vals[38] == "Nonaktif", "Indopaket should be Nonaktif"
            
            # Handle parent/variation transitions
            parent_sku = row_vals[8]
            if parent_sku:
                # This is a new product start
                current_product_sku = parent_sku
                current_product_name = row_vals[1]
                assert current_product_name, "Parent row must have product name!"
                assert len(row_vals[2] or "") > 0, "Parent row must have product description!"
            else:
                # This is a subsequent variation row
                assert current_product_sku is not None, "Variation row found before any parent row!"
                assert row_vals[1] in ("", None), f"Subsequent variations MUST have EMPTY product name! Got: {row_vals[1]!r}"
                assert row_vals[2] in ("", None), f"Subsequent variations MUST have EMPTY product description! Got: {row_vals[2]!r}"
                assert row_vals[8] in ("", None), f"Subsequent variations MUST have EMPTY SKU parent! Got: {row_vals[8]!r}"
            
            assert row_vals[10] == current_product_sku, f"Integration SKU must match current product SKU! Got {row_vals[10]!r}, expected {current_product_sku!r}"
            
        print("\nAll assertions passed successfully!")
        
    except Exception as e:
        print(f"❌ Error during verification: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Rollback the transaction to keep the database completely clean
        print("Rolling back database transaction...")
        db.rollback()
        db.close()

if __name__ == '__main__':
    main()
