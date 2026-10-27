"""L2 knowledge: read the environment the agent operates on (techhub.db)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "systems" / "techhub" / "data" / "structured" / "techhub.db"


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    email: str
    phone: str
    city: str
    segment: str


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    order_date: str
    status: str
    total: float


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    category: str
    price: float
    in_stock: bool


def _rows(sql: str, *params) -> list[tuple]:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()


@cache
def customers() -> list[Customer]:
    return [Customer(*r) for r in _rows(
        "SELECT customer_id, name, email, phone, city, segment FROM customers ORDER BY customer_id")]


@cache
def orders() -> list[Order]:
    return [Order(*r) for r in _rows(
        "SELECT order_id, customer_id, order_date, status, total_amount FROM orders ORDER BY order_date")]


@cache
def products() -> list[Product]:
    return [Product(r[0], r[1], r[2], r[3], bool(r[4])) for r in _rows(
        "SELECT product_id, name, category, price, in_stock FROM products ORDER BY product_id")]


def orders_of(customer_id: str) -> list[Order]:
    return [o for o in orders() if o.customer_id == customer_id]


def latest_order(customer_id: str) -> Order | None:
    own = orders_of(customer_id)
    return own[-1] if own else None


def spend_by_category(customer_id: str, category: str) -> float:
    (total,) = _rows(
        """SELECT COALESCE(SUM(oi.quantity * oi.price_per_unit), 0)
           FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
           JOIN products p ON p.product_id = oi.product_id
           WHERE o.customer_id = ? AND p.category = ? AND o.status != 'Cancelled'""",
        customer_id, category,
    )[0]
    return float(total)


@cache
def purchases() -> list[tuple[Customer, Product]]:
    """(customer, product) pairs for every delivered purchase."""
    by_c = {c.customer_id: c for c in customers()}
    by_p = {p.product_id: p for p in products()}
    rows = _rows("""SELECT DISTINCT o.customer_id, oi.product_id FROM orders o
                    JOIN order_items oi ON oi.order_id = o.order_id
                    WHERE o.status = 'Delivered' ORDER BY 1, 2""")
    return [(by_c[c], by_p[p]) for c, p in rows]


def money_forms(amount: float) -> list[str]:
    """Ways a reply might write an amount: $1,399.00, 1,399.00, 1399.00, $1,399."""
    whole = f"{amount:,.0f}" if amount == int(amount) else None
    forms = [f"${amount:,.2f}", f"{amount:,.2f}", f"{amount:.2f}"]
    return forms + ([f"${whole}"] if whole else [])
