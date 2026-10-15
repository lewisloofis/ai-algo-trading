"""runner — unattended daily runner (a scheduler/supervisor around existing commands).

Paper by default. It trades live only for a 'candidate' strategy the operator armed for today
(runner.arm), decided in ONE place (runner.modes.decide_mode) and executed through ONE gate
(runner.guard): every child command passes runner.guard first."""
