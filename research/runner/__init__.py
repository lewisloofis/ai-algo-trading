"""runner — unattended daily PAPER runner (a scheduler/supervisor around existing commands).

Never arms live trading: every child command passes runner.guard first."""
