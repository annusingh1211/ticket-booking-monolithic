from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.food import FoodItem

FOODS = [
    ("Veg Burger", 149.00),
    ("Paneer Wrap", 179.00),
    ("Veg Pizza", 249.00),
    ("Margherita Pizza", 229.00),
    ("Paneer Tikka", 199.00),
    ("Veg Sandwich", 129.00),
    ("French Fries", 99.00),
    ("Masala Fries", 119.00),
    ("Veg Momos", 139.00),
    ("Cheese Nachos", 159.00),
    ("Masala Maggi", 99.00),
    ("Veg Pasta", 189.00),
    ("Chole Kulche", 149.00),
    ("Rajma Rice", 169.00),
    ("Paneer Biryani", 219.00),
    ("Veg Biryani", 189.00),
    ("Cold Coffee", 119.00),
    ("Fresh Lime Soda", 89.00),
    ("Mango Juice", 99.00),
    ("Chocolate Brownie", 129.00),
]


def main() -> None:
    db = SessionLocal()
    created = skipped = 0
    try:
        for name, price in FOODS:
            existing = db.execute(
                select(FoodItem).where(FoodItem.name == name)
            ).scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            db.add(FoodItem(name=name, price=price, available=True))
            created += 1

        db.commit()
        print(f"Food seed complete: created={created}, skipped={skipped}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
