# inv

A tiny CSV-backed inventory.

```
python3 -m inv add bolts 100 0.05     # new item: name, qty, price
python3 -m inv add bolts 50           # restock
python3 -m inv list                   # table sorted by name, with the total value
python3 -m inv list --format json     # the same items as a JSON array
python3 -m inv list --below 20        # only items with qty below 20 (any format)
python3 -m inv --db other.csv list    # another inventory file
```
