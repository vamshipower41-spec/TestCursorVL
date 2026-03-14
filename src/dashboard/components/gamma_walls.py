"""Price chart with gamma wall overlays."""

from __future__ import annotations

import plotly.graph_objects as go
import pandas as pd

from src.data.models import GEXProfile


def render_price_with_walls(
    price_data: pd.DataFrame,
    gex_profile: GEXProfile,
) -> go.Figure:
    """Render a candlestick/line chart with gamma wall levels overlaid.

    Args:
        price_data: DataFrame with timestamp, open, high, low, close (or just ltp)
        gex_profile: Current GEX profile with computed levels
    """
    fig = go.Figure()

    # Price line (or candlestick if OHLC available)
    if "close" in price_data.columns:
        fig.add_trace(go.Candlestick(
            x=price_data["timestamp"],
            open=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            name="Price",
        ))
    elif "ltp" in price_data.columns:
        fig.add_trace(go.Scatter(
            x=price_data["timestamp"],
            y=price_data["ltp"],
            mode="lines",
            name="Spot",
            line=dict(color="white", width=2),
        ))

    # Call wall (resistance) — green
    if gex_profile.call_wall:
        fig.add_hline(
            y=gex_profile.call_wall,
            line_dash="dash",
            line_color="#26a69a",
            line_width=1.5,
            annotation_text=f"Call Wall: {gex_profile.call_wall:.0f}",
            annotation_position="top right",
            annotation_font_color="#26a69a",
        )

    # Put wall (support) — red
    if gex_profile.put_wall:
        fig.add_hline(
            y=gex_profile.put_wall,
            line_dash="dash",
            line_color="#ef5350",
            line_width=1.5,
            annotation_text=f"Put Wall: {gex_profile.put_wall:.0f}",
            annotation_position="bottom right",
            annotation_font_color="#ef5350",
        )

    # Gamma flip — yellow
    if gex_profile.gamma_flip_level:
        fig.add_hline(
            y=gex_profile.gamma_flip_level,
            line_dash="dot",
            line_color="#ffc107",
            line_width=1.5,
            annotation_text=f"Gamma Flip: {gex_profile.gamma_flip_level:.0f}",
            annotation_position="bottom left",
            annotation_font_color="#ffc107",
        )

    # Max gamma — blue
    if gex_profile.max_gamma_strike:
        fig.add_hline(
            y=gex_profile.max_gamma_strike,
            line_dash="dot",
            line_color="#2196f3",
            line_width=1.5,
            annotation_text=f"Max Gamma: {gex_profile.max_gamma_strike:.0f}",
            annotation_position="top left",
            annotation_font_color="#2196f3",
        )

    fig.update_layout(
        title=dict(
            text=f"{gex_profile.instrument} Price with Gamma Levels",
            font=dict(size=16),
        ),
        xaxis_title="Time",
        yaxis_title="Price",
        template="plotly_dark",
        height=400,
        xaxis_rangeslider_visible=False,
        margin=dict(l=50, r=20, t=50, b=30),
        font=dict(size=13),
    )
    fig.update_annotations(font_size=13)

    return fig
