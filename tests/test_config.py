import importlib
import logging
import os

import pytest


def _reload_config(monkeypatch, **env):
    for k in (
        "CRT_MARGIN_TOP", "CRT_MARGIN_BOTTOM",
        "CRT_MARGIN_LEFT", "CRT_MARGIN_RIGHT",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import crt.config as config
    importlib.reload(config)
    return config


def test_margins_default_to_zero(monkeypatch):
    cfg = _reload_config(monkeypatch)
    assert cfg.MARGIN_TOP == 0
    assert cfg.MARGIN_BOTTOM == 0
    assert cfg.MARGIN_LEFT == 0
    assert cfg.MARGIN_RIGHT == 0


def test_margins_read_from_env(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        CRT_MARGIN_TOP="10", CRT_MARGIN_BOTTOM="15",
        CRT_MARGIN_LEFT="5", CRT_MARGIN_RIGHT="8",
    )
    assert (cfg.MARGIN_TOP, cfg.MARGIN_BOTTOM) == (10, 15)
    assert (cfg.MARGIN_LEFT, cfg.MARGIN_RIGHT) == (5, 8)


def test_margins_negative_clamped_to_zero(monkeypatch):
    cfg = _reload_config(monkeypatch, CRT_MARGIN_TOP="-10")
    assert cfg.MARGIN_TOP == 0


def test_margins_invalid_string_fallback_to_zero(monkeypatch):
    cfg = _reload_config(monkeypatch, CRT_MARGIN_TOP="not-a-number")
    assert cfg.MARGIN_TOP == 0


def test_vertical_margins_clamped_to_half_frame(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="config")
    cfg = _reload_config(
        monkeypatch, CRT_MARGIN_TOP="500", CRT_MARGIN_BOTTOM="500",
    )
    # 500+500=1000, max_sum=288, factor=0.288 → each becomes round(500*0.288)=144
    assert cfg.MARGIN_TOP + cfg.MARGIN_BOTTOM <= 288
    assert cfg.MARGIN_TOP == 144
    assert cfg.MARGIN_BOTTOM == 144
    assert any("exceed 50%" in r.getMessage() for r in caplog.records)


def test_horizontal_margins_clamped_to_half_frame(monkeypatch):
    cfg = _reload_config(
        monkeypatch, CRT_MARGIN_LEFT="400", CRT_MARGIN_RIGHT="400",
    )
    assert cfg.MARGIN_LEFT + cfg.MARGIN_RIGHT <= 384
    assert cfg.MARGIN_LEFT == 192
    assert cfg.MARGIN_RIGHT == 192


def test_vertical_margins_asymmetric_round_respects_max_sum(monkeypatch):
    cfg = _reload_config(
        monkeypatch, CRT_MARGIN_TOP="77", CRT_MARGIN_BOTTOM="371",
    )
    # 77+371=448, factor=288/448=0.6428..., round gives 50+239=289 without clamp.
    # With the clamp we guarantee sum <= 288.
    assert cfg.MARGIN_TOP + cfg.MARGIN_BOTTOM <= 288
