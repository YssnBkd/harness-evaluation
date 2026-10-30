shop/catalog.py:37: pages are numbered from 1, so page 1 starts at index per_page: the first page of items is skipped (off-by-one; should be (page - 1) * per_page)
shop/catalog.py:21: floor division drops the last partial page: 41 items at 20 per page gives 2 pages instead of 3 (needs ceiling division)
shop/catalog.py:28: max_price=0 is falsy and is treated as no limit, so a zero price cap returns every product (check `max_price is None`)
