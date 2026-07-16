"""
OpenDSS microgrid model interface using dss-python.

Provides methods to compile the DSS model, set component powers at each
time step, solve power flow, and extract simulation results.
Requires dss-python (pip install dss-python).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from dss import DSS
    HAS_OPENDSS = True
except ImportError:
    HAS_OPENDSS = False
    DSS = None
    logger.warning("dss-python not installed; only text file generation available")


class MicrogridModel:
    """
    Python interface to the OpenDSS microgrid model.

    Controls the time-series simulation loop in snapshot mode:
    at each hour, sets PV/wind/load/storage power, solves power flow,
    and extracts bus voltages and component powers.
    """

    def __init__(self, base_kv=0.4, base_freq=50):
        self.base_kv = base_kv
        self.base_freq = base_freq
        self._compiled = False

    @property
    def is_ready(self):
        return self._compiled

    def compile(self, dss_file="model/master.dss"):
        """
        Compile the OpenDSS model from a master DSS file.

        Args:
            dss_file: Path to the master DSS file

        Raises:
            RuntimeError: If dss-python is not installed or
                          compilation fails
        """
        if not HAS_OPENDSS:
            raise RuntimeError(
                "dss-python is required. Install with:\n"
                "  pip install dss-python"
            )

        dss_file_path = Path(dss_file)
        if not dss_file_path.exists():
            raise FileNotFoundError(f"DSS model file not found: {dss_file_path}")

        DSS.Text.Command = "Clear"
        DSS.Text.Command = f"Compile {dss_file_path}"

        if DSS.Error.Number == 0:
            self._compiled = True
            logger.info(f"Model compiled successfully from: {dss_file_path}")
            self._set_snapshot_mode()
        else:
            err_msg = DSS.Error.Description
            raise RuntimeError(
                f"Failed to compile model: {dss_file_path}\n"
                f"DSS Error: {err_msg}"
            )

    def _set_snapshot_mode(self):
        DSS.Text.Command = "Set Mode=Snap"
        DSS.Text.Command = "Set ControlMode=Static"

    def set_power(self, pv_kw, wind_kw, load_kw, storage_kw):
        """
        Set all component powers for the current time step.

        Args:
            pv_kw: PV generation (kW)
            wind_kw: Wind generation (kW)
            load_kw: EV charger load (kW)
            storage_kw: Storage power (kW, negative = charging)
        """
        DSS.Text.Command = f"Edit Generator.PV1 kW={pv_kw:.3f}"
        DSS.Text.Command = f"Edit Generator.Wind1 kW={wind_kw:.3f}"
        DSS.Text.Command = f"Edit Load.EVLoad kW={load_kw:.3f}"
        DSS.Text.Command = f"Edit Storage.Storage1 kW={storage_kw:.3f}"

    def solve(self):
        """
        Solve the power flow for the current snapshot.

        Raises:
            RuntimeError: If power flow does not converge
        """
        DSS.ActiveCircuit.Solution.Solve()
        if not DSS.ActiveCircuit.Solution.Converged:
            raise RuntimeError(
                "Power flow did not converge. Check component data and model."
            )

    def get_main_bus_voltage(self):
        """Return MainBus voltage in per-unit."""
        try:
            DSS.ActiveCircuit.SetActiveBus("MainBus")
            voltages = DSS.ActiveCircuit.ActiveBus.puVmagAngle
            if voltages and len(voltages) > 0:
                return voltages[0]
            return 1.0
        except Exception:
            return 1.0

    def get_source_power_kw(self):
        """
        Get Vsource (slack bus) power.
        In islanded mode, this should be close to zero if storage
        dispatch correctly balances the system.
        """
        try:
            DSS.ActiveCircuit.SetActiveElement("Vsource.source")
            powers = DSS.ActiveCircuit.ActiveCktElement.Powers
            total_kw = sum(powers[0::2])
            return total_kw
        except Exception:
            return 0.0

    def get_line_losses_kw(self):
        """Get total line losses across all branches (Losses is in Watts)."""
        total = 0.0
        for line_name in ["Line.PV_Line", "Line.Wind_Line",
                          "Line.Load_Line", "Line.Storage_Line"]:
            try:
                DSS.ActiveCircuit.SetActiveElement(line_name)
                losses = DSS.ActiveCircuit.ActiveCktElement.Losses
                total += losses[0] / 1000.0
            except Exception:
                pass
        return total


# ---- Text-file generation (for inspection / fallback) ---- #

def generate_dss_text(base_kv=0.4):
    """
    Generate the master DSS file text programmatically.

    Useful for inspection or when dss-python is not available.
    """
    return f"""Clear

Set DefaultBaseFrequency=50

New Circuit.ChargingStation_Microgrid bus1=MainBus basekV={base_kv} pu=1.0 phases=3

! Voltage source as system reference (slack bus) - auto-created
! by New Circuit command (named "source").

! Line code: 0.4 kV bus-level connection (low-impedance)
New LineCode.LV_Cable nphases=3 r1=0.01 x1=0.01 r0=0.01 x0=0.01 units=km

! PV branch (3000 kW rated)
New Line.PV_Line bus1=MainBus bus2=PV_Bus linecode=LV_Cable length=0.05 units=km
New Generator.PV1 bus1=PV_Bus phases=3 kV={base_kv} kW=0 model=1

! Wind branch (2000 kW rated)
New Line.Wind_Line bus1=MainBus bus2=Wind_Bus linecode=LV_Cable length=0.05 units=km
New Generator.Wind1 bus1=Wind_Bus phases=3 kV={base_kv} kW=0 model=1

! EV charger load branch (max 14400 kW)
New Line.Load_Line bus1=MainBus bus2=Load_Bus linecode=LV_Cable length=0.05 units=km
New Load.EVLoad bus1=Load_Bus phases=3 kV={base_kv} kW=0 model=1

! Energy storage branch (3750 kW PCS, 7500 kWh)
New Line.Storage_Line bus1=MainBus bus2=Storage_Bus linecode=LV_Cable length=0.05 units=km
New Storage.Storage1 bus1=Storage_Bus phases=3 kV={base_kv} kWrated=3750 kWhrated=7500 %stored=50 %reserve=10 %EffCharge=95 %EffDischarge=95 state=idling

Set Voltagebases=[{base_kv}]
CalcVoltageBases
"""
