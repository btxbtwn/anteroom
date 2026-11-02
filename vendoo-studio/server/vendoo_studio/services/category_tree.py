"""The six seeded category trees: readiness and Depop product types."""
from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode

MARKETPLACES = ("general", "ebay", "poshmark", "mercari", "depop", "etsy")


def categories_ready(db) -> bool:
    """Whether all six shared category trees are loaded and complete."""
    from sqlalchemy import func, select

    return db.scalar(select(func.count()).select_from(CategoryTree).where(
        CategoryTree.marketplace.in_(MARKETPLACES), CategoryTree.status == 'complete')) == len(MARKETPLACES)


def product_type_slugs(category_ids) -> list[str]:
    """Depop leaf ids are ``department_product-type``. The form stores the suffix."""
    slugs: set[str] = set()
    for category_id in category_ids:
        text = str(category_id or "").strip()
        if not text:
            continue
        slug = text.split("_", 1)[1].strip() if "_" in text else text
        if slug:
            slugs.add(slug)
    return sorted(slugs)


def depop_product_types(db) -> list[str]:
    rows = (
        db.query(CategoryTreeNode.category_id)
        .filter(CategoryTreeNode.marketplace == "depop", CategoryTreeNode.is_leaf.is_(True))
        .all()
    )
    return product_type_slugs(category_id for (category_id,) in rows)
