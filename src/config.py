"""config.yaml 로더 + 전략 팩토리."""
import yaml

from .data.factors import FactorProvider, load_factor_panel
from .data.fundamentals import QualityProvider, load_quality_panel
from .strategy.korea_multifactor import KoreaMultiFactor
from .strategy.multifactor_momentum import MultiFactorMomentum

_STRATEGIES = {
    "korea_multifactor": KoreaMultiFactor,
    "multifactor_momentum": MultiFactorMomentum,
}

_PANEL_FACTORS = ("value", "quality", "size", "supply_demand")


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_strategy(config: dict, **kwargs):
    name = config["strategy"]["name"]
    if name not in _STRATEGIES:
        raise ValueError(f"알 수 없는 전략: {name}. 가능: {list(_STRATEGIES)}")

    weights = config["strategy"].get("factor_weights", {})

    if name == "korea_multifactor":
        providers = {}
        for fac in _PANEL_FACTORS:
            if weights.get(fac, 0):
                providers[fac] = FactorProvider(load_factor_panel(fac))
        return KoreaMultiFactor(config, providers=providers)

    quality_provider = kwargs.get("quality_provider")
    if quality_provider is None and "quality" in weights:
        quality_provider = QualityProvider(load_quality_panel())
    return MultiFactorMomentum(config, quality_provider=quality_provider)
