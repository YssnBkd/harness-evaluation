members/db.py:45: SQL injection: name_prefix comes from the search box and is formatted into the query with an f-string; pass it as a parameter (LIKE ? with name_prefix + '%')
members/db.py:52: the SELECT returns (id, name, email) but the dict reads row[1] as email and row[2] as name, so name and email are swapped
members/db.py:32: a user who never logged in has no rows, so rows[0] raises IndexError instead of returning None as documented
