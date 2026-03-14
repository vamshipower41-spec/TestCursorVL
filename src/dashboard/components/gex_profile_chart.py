"""GEX Profile horizontal bar chart component."""

from __future__ import annotations

import plotly.graph_objects as go

from src.data.models import GEXProfile


def render_gex_profile(gex_profile: GEXProfile) -> go.Figure:
    """Render the GEX profile as a horizontal bar chart.

    - Y-axis: strike prices
    - X-axis: net GEX value
    - Green bars: positive net GEX (call-dominated, resistance)
    - Red bars: negative net GEX (put-dominated, support)
    - Horizontal line at spot price
    - Annotations at gamma flip, max gamma, call/put walls
    """
    strikes = [s.strike_price for s in gex_profile.strikes]
    net_gex = [s.net_gex for s in gex_profile.strikes]
    colors = ["#26a69a" if g >= 0 else "#ef5350" for g in net_gex]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        y=strikes,
        x=net_gex,
        orientation="h",
        marker_color=colors,
        name="Net GEX",
        hovertemplate="Strike: %{y}<br>Net GEX: %{x:,.0f}<extra></extra>",
    ))

    # Spot price line
    fig.add_hline(
        y=gex_profile.spot_price,
        line_dash="dash",
        line_color="white",
        line_width=2,
        annotation_text=f"Spot: {gex_profile.spot_price:.0f}",
        annotation_position="top right",
    )

    # Gamma flip level
    if gex_profile.gamma_flip_level:
        fig.add_hline(
            y=gex_profile.gamma_flip_level,
            line_dash="dot",
            line_color="#ffc107",
            line_width=1.5,
            annotation_text=f"Gamma Flip: {gex_profile.gamma_flip_level:.0f}",
            annotation_position="bottom right",
        )

    # Max gamma strike
    if gex_profile.max_gamma_strike:
        fig.add_hline(
            y=gex_profile.max_gamma_strike,
            line_dash="dot",
            line_color="#2196f3",
            line_width=1.5,
            annotation_text=f"Max Gamma: {gex_profile.max_gamma_strike:.0f}",
            annotation_position="top left",
        )

    fig.update_layout(
        title=dict(
            text=f"{gex_profile.instrument} GEX Profile | {gex_profile.expiry_date}",
            font=dict(size=16),
        ),
        xaxis_title="Net Gamma Exposure",
        yaxis_title="Strike Price",
        template="plotly_dark",
        height=450,
        showlegend=False,
        margin=dict(l=70, r=20, t=50, b=30),
        font=dict(size=13),
    )
    fig.update_annotations(font_size=14)

    return fig
