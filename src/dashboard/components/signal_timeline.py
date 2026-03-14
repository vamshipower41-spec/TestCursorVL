"""Signal event timeline chart component."""

from __future__ import annotations

import plotly.graph_objects as go

from src.data.models import GEXSignal


SIGNAL_COLORS = {
    "gamma_flip": "#ffc107",
    "pin_risk": "#2196f3",
    "breakout": "#e91e63",
    "vol_crush": "#9c27b0",
    "zero_gex_instability": "#ff9800",
}

SIGNAL_SYMBOLS = {
    "gamma_flip": "diamond",
    "pin_risk": "circle",
    "breakout": "triangle-up",
    "vol_crush": "square",
    "zero_gex_instability": "star",
}


def render_signal_timeline(signals: list[GEXSignal]) -> go.Figure:
    """Render signals on a timeline scatter plot.

    X-axis: time
    Y-axis: signal type (categorical)
    Color: signal type
    Size: proportional to strength
    """
    if not signals:
        fig = go.Figure()
        fig.update_layout(
            title="No signals to display",
            template="plotly_dark",
            height=300,
        )
        return fig

    fig = go.Figure()

    # Group by signal type
    by_type: dict[str, list[GEXSignal]] = {}
    for sig in signals:
        by_type.setdefault(sig.signal_type, []).append(sig)

    for sig_type, sigs in by_type.items():
        color = SIGNAL_COLORS.get(sig_type, "#ffffff")
        symbol = SIGNAL_SYMBOLS.get(sig_type, "circle")

        fig.add_trace(go.Scatter(
            x=[s.timestamp for s in sigs],
            y=[s.signal_type.replace("_", " ").title() for s in sigs],
            mode="markers",
            name=sig_type.replace("_", " ").title(),
            marker=dict(
                color=color,
                size=[max(s.strength * 24, 12) for s in sigs],
                symbol=symbol,
                line=dict(width=1.5, color="white"),
            ),
            hovertemplate=(
                "Time: %{x}<br>"
                "Type: %{y}<br>"
                "Level: %{customdata[0]:.2f}<br>"
                "Strength: %{customdata[1]:.2f}<br>"
                "Direction: %{customdata[2]}<extra></extra>"
            ),
            customdata=[
                [s.level, s.strength, s.direction or "neutral"]
                for s in sigs
            ],
        ))

    fig.update_layout(
        title=dict(text="Signal Timeline", font=dict(size=16)),
        xaxis_title="Time",
        yaxis_title="",
        template="plotly_dark",
        height=250,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=12)),
        margin=dict(l=100, r=20, t=60, b=30),
        font=dict(size=13),
    )

    return fig
