"""Gamma Blast alert card component for the scalping dashboard."""

from __future__ import annotations

import streamlit as st

from src.data.models import GammaBlast


def render_blast_alert(blast: GammaBlast) -> None:
    """Render a prominent gamma blast alert card with all details."""
    is_bull = blast.direction == "bullish"
    color = "#26a69a" if is_bull else "#ef5350"
    bg = "#0d2818" if is_bull else "#2d0a0a"
    arrow = "BULLISH" if is_bull else "BEARISH"
    icon = "UP" if is_bull else "DOWN"

    st.markdown(f"""
    <div style="
        background: {bg};
        border: 2px solid {color};
        border-radius: 12px;
        padding: 20px;
        margin: 16px 0;
        text-align: center;
    ">
        <div style="font-size: 2rem; font-weight: 900; color: {color};">
            GAMMA BLAST {icon}
        </div>
        <div style="font-size: 1.4rem; color: {color}; margin: 4px 0;">
            {arrow} &mdash; Score: {blast.composite_score:.0f}/100
        </div>
        <div style="font-size: 0.95rem; color: #ccc; margin-top: 8px;">
            {blast.instrument} | {blast.timestamp:%H:%M:%S} IST
            | TTE: {blast.time_to_expiry_hours:.1f}h
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Trade levels
    cols = st.columns(3)
    cols[0].metric("Entry", f"{blast.entry_level:,.2f}")
    cols[1].metric("Stop Loss", f"{blast.stop_loss:,.2f}",
                   delta=f"{abs(blast.entry_level - blast.stop_loss):,.0f} pts",
                   delta_color="inverse")
    cols[2].metric("Target", f"{blast.target:,.2f}",
                   delta=f"{abs(blast.target - blast.entry_level):,.0f} pts",
                   delta_color="normal")


def render_blast_components(blast: GammaBlast) -> None:
    """Render the breakdown of individual model scores."""
    st.markdown("**Model Breakdown:**")

    for comp in sorted(blast.components, key=lambda c: c.score * c.weight, reverse=True):
        weighted = comp.score * comp.weight
        bar_width = min(int(comp.score), 100)

        if comp.score >= 70:
            bar_color = "#26a69a"
        elif comp.score >= 40:
            bar_color = "#ffc107"
        else:
            bar_color = "#666"

        label = comp.model_name.replace("_", " ").title()

        st.markdown(f"""
        <div style="margin: 6px 0;">
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem;">
                <span>{label}</span>
                <span style="color: {bar_color};">{comp.score:.0f} x {comp.weight:.0%} = {weighted:.0f}</span>
            </div>
            <div style="background: #333; border-radius: 4px; height: 8px; margin-top: 2px;">
                <div style="background: {bar_color}; width: {bar_width}%; height: 100%;
                            border-radius: 4px;"></div>
            </div>
            <div style="font-size: 0.75rem; color: #888; margin-top: 2px;">{comp.detail}</div>
        </div>
        """, unsafe_allow_html=True)


def render_no_blast_status(
    instrument: str,
    is_expiry_day: bool,
    time_to_expiry_hours: float,
    fired_today: int,
    max_signals: int,
) -> None:
    """Render the waiting/inactive status when no blast is detected."""
    if not is_expiry_day:
        st.markdown("""
        <div style="background: #1a1a2e; border: 1px solid #444; border-radius: 12px;
                    padding: 24px; text-align: center; margin: 16px 0;">
            <div style="font-size: 1.5rem; color: #888;">NOT EXPIRY DAY</div>
            <div style="font-size: 0.9rem; color: #666; margin-top: 8px;">
                Gamma Blast detection is only active on expiry days.<br>
                NIFTY: Tuesday | SENSEX: Thursday
            </div>
        </div>
        """, unsafe_allow_html=True)
        return

    if fired_today >= max_signals:
        st.markdown(f"""
        <div style="background: #1a2e1a; border: 1px solid #26a69a; border-radius: 12px;
                    padding: 24px; text-align: center; margin: 16px 0;">
            <div style="font-size: 1.3rem; color: #26a69a;">
                MAX SIGNALS REACHED ({fired_today}/{max_signals})
            </div>
            <div style="font-size: 0.9rem; color: #888; margin-top: 8px;">
                Daily limit hit. No more blast signals today.
            </div>
        </div>
        """, unsafe_allow_html=True)
        return

    # Actively scanning
    pulse_color = "#ffc107" if time_to_expiry_hours < 3 else "#4a90d9"
    st.markdown(f"""
    <div style="background: #1a1a2e; border: 1px solid {pulse_color}; border-radius: 12px;
                padding: 24px; text-align: center; margin: 16px 0;">
        <div style="font-size: 1.5rem; color: {pulse_color};">
            SCANNING FOR GAMMA BLAST...
        </div>
        <div style="font-size: 0.9rem; color: #888; margin-top: 8px;">
            {instrument} | TTE: {time_to_expiry_hours:.1f}h
            | Signals today: {fired_today}/{max_signals}
        </div>
        <div style="font-size: 0.8rem; color: #666; margin-top: 4px;">
            Composite score must reach 70+ from 6 confirming models
        </div>
    </div>
    """, unsafe_allow_html=True)
