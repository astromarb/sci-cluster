from decimal import Decimal
import tomli


toml_str = """
           gretzky = 99

           [kurri]
           jari = 17
           """

toml_dict1 = tomli.loads(toml_str)
assert toml_dict1 == {"gretzky": 99, "kurri": {"jari": 17}}

with open("example.toml", "rb") as f:
    toml_dict2 = tomli.load(f)
assert isinstance(toml_dict2 , dict)

toml_dict3 = tomli.loads("precision-matters = 0.982492", parse_float=Decimal)
assert toml_dict3["precision-matters"] == Decimal("0.982492")