"""
基于 dss-python 的 OpenDSS 微电网模型接口。

提供编译 DSS 模型、设置各时间步长组件功率、求解潮流
以及提取仿真结果的方法。
需要 dss-python (pip install dss-python)。
"""

import logging
import math
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    from dss import DSS
    HAS_OPENDSS = True
except ImportError:
    HAS_OPENDSS = False
    DSS = None
    logger.warning("dss-python 未安装; 仅支持生成文本文件")


class MicrogridModel:
    """
    OpenDSS 微电网模型的 Python 接口。

    以快照模式控制时序仿真循环:
    每小时内设置光伏/风电/负荷/储能功率，求解潮流，
    并提取母线电压和各组件功率。
    """

    def __init__(self, base_kv=0.4, base_freq=50):
        self.base_kv = base_kv
        self.base_freq = base_freq
        self._compiled = False
        self._islanded_mode = False

    @property
    def is_ready(self):
        return self._compiled

    def compile(self, dss_file="model/master.dss", islanded=False,
                vtheta_bus="Storage_Bus"):
        """
        从主 DSS 文件编译 OpenDSS 模型。

        Args:
            dss_file: 主 DSS 文件路径

        Raises:
            RuntimeError: 若 dss-python 未安装或编译失败
        """
        if not HAS_OPENDSS:
            raise RuntimeError(
                "需要安装 dss-python。请执行:\n"
                "  pip install dss-python"
            )

        dss_file_path = Path(dss_file)
        if not dss_file_path.exists():
            raise FileNotFoundError(f"DSS 模型文件未找到: {dss_file_path}")

        DSS.Text.Command = "Clear"
        DSS.Text.Command = f"Compile {dss_file_path}"

        if DSS.Error.Number == 0:
            self._compiled = True
            logger.info(f"模型编译成功: {dss_file_path}")
            self._set_snapshot_mode()
            self._islanded_mode = bool(islanded)
            if islanded:
                DSS.Text.Command = f"Edit Vsource.source bus1={vtheta_bus}"
                if DSS.Error.Number != 0:
                    raise RuntimeError(
                        f"无法将数值 Vtheta 参考移至 {vtheta_bus}: "
                        f"{DSS.Error.Description}"
                    )
        else:
            err_msg = DSS.Error.Description
            raise RuntimeError(
                f"模型编译失败: {dss_file_path}\n"
                f"DSS 错误: {err_msg}"
            )

    def _set_snapshot_mode(self):
        DSS.Text.Command = "Set Mode=Snap"
        DSS.Text.Command = "Set ControlMode=Static"

    def set_power(self, pv_kw, wind_kw, load_kw, storage_kw):
        """
        设置当前时间步长所有组件的功率。

        Args:
            pv_kw: 光伏发电 (kW)
            wind_kw: 风电发电 (kW)
            load_kw: 充电桩负荷 (kW)
            storage_kw: 储能功率 (kW, 负值 = 充电)
        """
        DSS.Text.Command = f"Edit Generator.PV1 kW={pv_kw:.3f}"
        DSS.Text.Command = f"Edit Generator.Wind1 kW={wind_kw:.3f}"
        DSS.Text.Command = f"Edit Load.EVLoad kW={load_kw:.3f}"
        DSS.Text.Command = f"Edit Storage.Storage1 kW={storage_kw:.3f}"

    def set_islanded_power(self, pv_kw, wind_kw, load_kw, load_kvar=0.0):
        """设置无外部电网模式的单站发电与负荷输入，不发储能 P 命令。"""
        DSS.Text.Command = f"Edit Generator.PV1 kW={pv_kw:.6f} kvar=0"
        DSS.Text.Command = f"Edit Generator.Wind1 kW={wind_kw:.6f} kvar=0"
        DSS.Text.Command = (
            f"Edit Load.EVLoad kW={load_kw:.6f} kvar={float(load_kvar):.6f}"
        )

    def set_storage_dispatch(self, p_discharge_kw, q_injection_kvar,
                             soc_pct, kwh_rated, kva_rated, idle_loss_kw=0.0):
        """设置 GFM BESS 命令。

        参数采用网络注入符号：P/Q 正值代表向网络注入。OpenDSS Storage
        命令符号相反，因此在本适配器内统一转换，并显式设置 kVA。
        """
        # Probed with dss-python 0.15.7: storage command kW>0 in
        # DISCHARGING produces negative terminal P (injection to the feeder).
        # Positive kvar command likewise produces negative terminal Q.
        if abs(p_discharge_kw) <= 1e-8:
            state = "IDLING"
        else:
            state = "DISCHARGING" if p_discharge_kw > 0 else "CHARGING"
        DSS.Text.Command = (
            f"Edit Storage.Storage1 kW={float(p_discharge_kw):.6f} "
            f"kvar={float(q_injection_kvar):.6f} state={state} "
            f"kWhrated={float(kwh_rated):.6f} kVA={float(kva_rated):.6f} "
            f"%stored={float(soc_pct):.6f} %IdlingkW={float(idle_loss_kw):.6f}"
        )

    def solve(self):
        """
        求解当前快照的潮流。

        Raises:
            RuntimeError: 若潮流不收敛
        """
        DSS.ActiveCircuit.Solution.Solve()
        if not DSS.ActiveCircuit.Solution.Converged:
            raise RuntimeError(
                "潮流未收敛。请检查组件数据及模型。"
            )

    def get_main_bus_voltage(self):
        """返回主母线电压 (标幺值)。"""
        try:
            DSS.ActiveCircuit.SetActiveBus("MainBus")
            voltages = DSS.ActiveCircuit.ActiveBus.puVmagAngle
            if voltages and len(voltages) > 0:
                return voltages[0]
            return 1.0
        except Exception:
            return 1.0

    def get_bus_voltage(self, bus_name):
        """返回指定母线的相电压幅值列表 (pu)。"""
        DSS.ActiveCircuit.SetActiveBus(str(bus_name))
        raw = list(DSS.ActiveCircuit.ActiveBus.puVmagAngle)
        return raw[0::2]

    def get_source_power_kw(self):
        """
        获取 Vsource (平衡节点) 功率。
        在孤岛模式下，若储能调度正确平衡系统，该值应接近于零。
        """
        try:
            DSS.ActiveCircuit.SetActiveElement("Vsource.source")
            powers = DSS.ActiveCircuit.ActiveCktElement.Powers
            total_kw = sum(powers[0::2])
            return total_kw
        except Exception:
            return 0.0

    def get_line_losses_kw(self):
        """获取所有支路的总线路损耗 (损耗单位为瓦特)。"""
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

    def set_source_reference(self, pu=1.0, angle_deg=0.0, frequency_hz=50.0):
        """设置数值 Vtheta 参考。

        该参考只用于 OpenDSS 网络方程，不代表外部电网供能。严格离网
        求解必须同时使用 ``IslandedGFMQSTS`` 并把 Vsource 有功 mismatch
        反馈到构网储能下垂外环。
        """
        if not HAS_OPENDSS:
            raise RuntimeError("dss-python 未安装")
        src = DSS.ActiveCircuit.Vsources
        src.First
        src.pu = float(pu)
        src.AngleDeg = float(angle_deg)
        src.Frequency = float(frequency_hz)
        DSS.ActiveCircuit.Solution.Frequency = float(frequency_hz)

    def set_storage_pq(self, p_kw, q_kvar, state="IDLING", soc_pct=None,
                       kwh_rated=None, kva_rated=None):
        """设置 OpenDSS Storage 的 P/Q 命令和可选额定容量。

        OpenDSS Storage 的 ``kW`` 是设备命令方向：正值放电、负值充电；
        元件端口 ``Powers`` 则以负值表示向网络注入。调用者应使用
        ``storage_command_kw`` 语义，禁止把端口注入符号直接复用为命令符号。
        """
        if not HAS_OPENDSS:
            raise RuntimeError("dss-python 未安装")
        if kwh_rated is not None or kva_rated is not None:
            fields = []
            if kwh_rated is not None:
                fields.append(f"kWhrated={float(kwh_rated):.6f}")
            if kva_rated is not None:
                fields.append(f"kVA={float(kva_rated):.6f}")
            DSS.Text.Command = "Edit Storage.Storage1 " + " ".join(fields)
        cmd = (
            f"Edit Storage.Storage1 kW={float(p_kw):.6f} "
            f"kvar={float(q_kvar):.6f} state={state}"
        )
        if soc_pct is not None:
            cmd += f" %stored={float(soc_pct):.6f}"
        DSS.Text.Command = cmd

    def get_element_power(self, element_name):
        """返回元件每相功率和 P/Q 总量，OpenDSS 元件注入为负。"""
        DSS.ActiveCircuit.SetActiveElement(element_name)
        powers = list(DSS.ActiveCircuit.ActiveCktElement.Powers)
        p_kw = sum(powers[0::2])
        q_kvar = sum(powers[1::2])
        return {"p_kw": p_kw, "q_kvar": q_kvar, "phases": powers}

    def get_source_mismatch(self):
        """返回数值参考源吸收的 P/Q mismatch。

        负 P 表示系统缺额由 slack 参考吸收；离网控制器必须消除该
        mismatch 或显式记录为负荷削减，不能把它计作外部供电。
        """
        return self.get_element_power("Vsource.source")

    def get_total_losses_kw_kvar(self):
        """返回电路总损耗 P/Q (kW/kvar)，用于 QSTS mismatch 计算。"""
        losses = list(DSS.ActiveCircuit.Losses)
        return {"p_kw": losses[0] / 1000.0, "q_kvar": losses[1] / 1000.0}

    def get_bus_voltage_range(self):
        """返回全网母线 pu 电压最小值、最大值及逐相明细。"""
        values = []
        details = {}
        for name in DSS.ActiveCircuit.AllBusNames:
            DSS.ActiveCircuit.SetActiveBus(name)
            raw = list(DSS.ActiveCircuit.ActiveBus.puVmagAngle)
            mags = raw[0::2]
            if mags:
                details[name] = mags
                values.extend(mags)
        return {
            "vmin_pu": min(values) if values else float("nan"),
            "vmax_pu": max(values) if values else float("nan"),
            "by_bus": details,
        }

    def get_line_current_loading(self, ampacity_overrides=None):
        """返回线路最大电流和可用额定电流比。

        没有真实 NormAmps/额定电流覆盖值时，只报告测得电流，不宣称
        线路热约束已通过。
        """
        ampacity_overrides = {
            str(k).lower(): float(v)
            for k, v in (ampacity_overrides or {}).items()
        }
        records = []
        for name in DSS.ActiveCircuit.Lines.AllNames:
            DSS.ActiveCircuit.SetActiveElement(f"Line.{name}")
            currents = list(DSS.ActiveCircuit.ActiveCktElement.CurrentsMagAng)
            amps = currents[0::2]
            amp_max = max(amps) if amps else 0.0
            DSS.ActiveCircuit.Lines.Name = name
            # OpenDSS may report a library default NormAmps even when the
            # actual conductor ampacity is unknown. Only user-supplied,
            # documented ratings count as verified thermal limits.
            rating = ampacity_overrides.get(name.lower())
            records.append({
                "line": name,
                "current_max_a": amp_max,
                "rating_a": rating,
                "rating_valid": rating is not None and rating > 0,
                "loading_pu": amp_max / rating if rating is not None and rating > 0 else None,
            })
        return records


@dataclass
class IslandedGFMConfig:
    """单站 GFM 储能和下垂控制配置。"""

    battery_kwh: float = 2000.0
    pcs_p_kw: float = 800.0
    pcs_q_kvar: Optional[float] = 600.0
    pcs_kva: Optional[float] = 1000.0
    pv_rated_kw: float = 400.0
    wind_rated_kw: float = 100.0
    ev_rated_kw: float = 14400.0
    soc_min: float = 0.10
    soc_max: float = 0.90
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    soc_initial: float = 0.50
    soc_reserve: float = 0.20
    storage_idle_loss_kw: float = 0.0
    f_ref_hz: float = 50.0
    f_min_hz: float = 49.5
    f_max_hz: float = 50.5
    v_ref_pu: float = 1.0
    v_min_pu: float = 0.95
    v_max_pu: float = 1.05
    vtheta_margin_pu: float = 0.01
    coupling: Optional[str] = "X_gt_R"  # 用户指定 P-f / Q-V
    delta_f_droop_hz: float = 0.5
    delta_v_droop_pu: float = 0.01
    vtheta_bus: str = "Storage_Bus"
    max_iterations: int = 40
    relaxation: float = 0.5
    voltage_tolerance_pu: float = 1e-4
    frequency_tolerance_hz: float = 1e-3
    power_tolerance_kw: float = 0.5
    virtual_impedance_mode: bool = False
    slack_external_power_tolerance_kw: float = 0.5
    line_current_limit_pu: float = 1.0
    line_ampacity_a: Dict[str, float] = field(default_factory=dict)
    enforce_voltage_limits: bool = True
    fail_on_unrated_lines: bool = False

    def __post_init__(self):
        if self.coupling not in {"X_gt_R", "R_gt_X"}:
            raise ValueError("coupling 必须是 X_gt_R 或 R_gt_X")
        if not 0 < self.relaxation <= 1:
            raise ValueError("relaxation 必须在 (0, 1] 内")
        if self.pcs_kva is not None and self.pcs_kva < self.pcs_p_kw:
            raise ValueError("PCS kVA 额定值不得低于 PCS kW 额定值")
        if self.pcs_kva is None:
            raise ValueError("必须从设备资料提供 PCS kVA 额定值")
        if self.pcs_q_kvar is None:
            raise ValueError("必须从设备资料提供 PCS 最大 kvar")
        if not self.soc_min <= self.soc_initial <= self.soc_max:
            raise ValueError("soc_initial 必须位于 SOC 安全范围内")
        if not self.soc_min <= self.soc_reserve <= self.soc_max:
            raise ValueError("soc_reserve 必须位于 SOC 安全范围内")


class IslandedGFMQSTS:
    """参考 Cunha et al. 的 OpenDSS 外层 GFM 下垂 QSTS 求解器。

    OpenDSS 的 Circuit Vsource 仅是数值电压/相角参考。每个时间步中，
    此类迭代 P-f/Q-V（X>R）或 P-V/Q-f（R>X）下垂、Vtheta 参考、BESS
    P/Q 与潮流，直到 mismatch、电压和频率收敛。未由 BESS 供给的有功
    缺额按 EV 负荷削减处理，并再次求解，不作为外部电网补给。
    """

    def __init__(self, model: MicrogridModel, config: IslandedGFMConfig):
        if not HAS_OPENDSS:
            raise RuntimeError("dss-python 未安装")
        self.model = model
        self.config = config
        self._soc = config.soc_initial
        self._frequency_hz = config.f_ref_hz
        self._vtheta_pu = config.v_ref_pu
        self._vtheta_angle_deg = 0.0

    def reset(self, soc_pct=None, frequency_hz=None, vtheta_pu=None):
        self._soc = (self.config.soc_initial if soc_pct is None
                     else float(soc_pct) / 100.0)
        self._frequency_hz = frequency_hz or self.config.f_ref_hz
        self._vtheta_pu = vtheta_pu or self.config.v_ref_pu
        self._vtheta_angle_deg = 0.0

    def _soc_limited_p(self, p_kw, dt_hours):
        c = self.config
        soc = self._soc
        soc_floor = max(c.soc_min, c.soc_reserve)
        p = max(-c.pcs_p_kw, min(c.pcs_p_kw, p_kw))
        if p > 0:
            max_discharge = max(0.0, (soc - soc_floor) * c.battery_kwh * c.eta_discharge / dt_hours)
            p = min(p, max_discharge)
        elif p < 0:
            max_charge = max(0.0, (c.soc_max - soc) * c.battery_kwh / (c.eta_charge * dt_hours))
            p = max(p, -max_charge)
        return p

    def _advance_soc(self, p_kw, dt_hours):
        c = self.config
        if p_kw >= 0:
            self._soc -= p_kw * dt_hours / (c.eta_discharge * c.battery_kwh)
        else:
            self._soc += (-p_kw) * dt_hours * c.eta_charge / c.battery_kwh
        self._soc = max(c.soc_min, min(c.soc_max, self._soc))

    def _slopes(self, p_available_kw):
        c = self.config
        if c.pcs_kva is None:
            raise ValueError("请提供 PCS 铭牌 pcs_kva 以建立 P-Q 视在功率约束")
        pnom = max(min(c.pcs_p_kw, max(p_available_kw, 0.05 * c.pcs_p_kw)), 1.0)
        q_nominal = c.pcs_q_kvar if c.pcs_q_kvar is not None else c.pcs_kva
        qnom = max(min(q_nominal, c.pcs_kva), 1.0)
        if c.coupling == "X_gt_R":
            return c.delta_f_droop_hz / pnom, c.delta_v_droop_pu / qnom
        return c.delta_v_droop_pu / pnom, c.delta_f_droop_hz / qnom

    def _droop_setpoints(self, p_schedule_kw, q_schedule_kvar, frequency_hz,
                         voltage_pu, p_slope, q_slope, dt_hours):
        """Cunha ESS droop equations; P/Q are positive network injections."""
        c = self.config
        soc_factor = max(self._soc, 0.05)
        if c.coupling == "X_gt_R":
            p = p_schedule_kw - soc_factor / max(p_slope, 1e-9) * (
                frequency_hz - c.f_ref_hz
            )
            q = q_schedule_kvar - (voltage_pu - c.v_ref_pu) / max(q_slope, 1e-9)
        else:
            p = p_schedule_kw - soc_factor / max(p_slope, 1e-9) * (
                voltage_pu - c.v_ref_pu
            )
            q = q_schedule_kvar - (frequency_hz - c.f_ref_hz) / max(q_slope, 1e-9)

        p = self._soc_limited_p(p, dt_hours)
        q_cap = min(c.pcs_q_kvar if c.pcs_q_kvar is not None else c.pcs_kva,
                    c.pcs_kva)
        q = max(-q_cap, min(q_cap, q))
        q_cap_from_p = math.sqrt(max(0.0, c.pcs_kva**2 - p**2))
        return p, max(-q_cap_from_p, min(q_cap_from_p, q))

    def _solve_network(self, pv_kw, wind_kw, ev_kw, p_bess_kw, q_bess_kvar,
                       non_ev_kw, ev_load_kvar, non_ev_kvar,
                       frequency_hz, vtheta_pu, angle_deg):
        c = self.config
        self.model.set_source_reference(vtheta_pu, angle_deg, frequency_hz)
        self.model.set_storage_dispatch(
            p_discharge_kw=p_bess_kw,
            q_injection_kvar=q_bess_kvar,
            soc_pct=self._soc * 100.0,
            kwh_rated=c.battery_kwh,
            kva_rated=c.pcs_kva,
            idle_loss_kw=c.storage_idle_loss_kw,
        )
        load_kw = max(non_ev_kw, 0.0) + min(max(ev_kw, 0.0), c.ev_rated_kw)
        total_load_kvar = ev_load_kvar + non_ev_kvar
        self.model.set_islanded_power(
            min(max(pv_kw, 0.0), c.pv_rated_kw),
            min(max(wind_kw, 0.0), c.wind_rated_kw),
            load_kw,
            total_load_kvar,
        )
        self.model.solve()
        source = self.model.get_source_mismatch()
        storage = self.model.get_element_power("Storage.Storage1")
        losses = self.model.get_total_losses_kw_kvar()
        return {
            "source_terminal": source,
            "storage_terminal": storage,
            "storage_injection": {
                "p_kw": -storage["p_kw"],
                "q_kvar": -storage["q_kvar"],
            },
            "losses": losses,
            "voltage": self.model.get_bus_voltage_range(),
            "currents": self.model.get_line_current_loading(c.line_ampacity_a),
            "losses_kw": losses["p_kw"],
        }

    def _solve_step_legacy_prototype(self, pv_kw, wind_kw, ev_base_kw, p_bess_schedule_kw=0.0,
                   q_bess_schedule_kvar=0.0, non_ev_kw=0.0, load_kvar=None,
                   dt_hours=1.0, allow_ev_shedding=True):
        """执行一个无外部补给的离网准静态潮流时步。"""
        c = self.config
        if dt_hours <= 0:
            raise ValueError("dt_hours 必须为正数")
        if c.pcs_kva is None:
            raise ValueError("GFM 离网求解需要设备铭牌 pcs_kva")
        ev_requested = max(0.0, float(ev_base_kw))
        ev_served = ev_requested
        if load_kvar is None:
            raise ValueError("solve_step 必须提供预测/站端的负荷无功 kvar")
        load_kvar = max(0.0, float(load_kvar))
        pv_used = min(max(float(pv_kw), 0.0), c.pv_rated_kw)
        wind_used = min(max(float(wind_kw), 0.0), c.wind_rated_kw)
        p_schedule = self._soc_limited_p(float(p_bess_schedule_kw), dt_hours)
        q_schedule = float(q_bess_schedule_kvar)
        p_droop, q_droop = self._slopes(c.pcs_p_kw)
        f = self._frequency_hz
        vtheta = self._vtheta_pu
        angle = self._vtheta_angle_deg
        p_bess, q_bess = p_schedule, q_schedule
        result = None
        ev_shed = 0.0
        pv_curtailed = 0.0
        wind_curtailed = 0.0
        converged = False
        p_residual = q_residual = float("inf")

        for iteration in range(1, c.max_iterations + 1):
            result = self._solve_network(
                pv_used, wind_used, ev_served, p_bess, q_bess, non_ev_kw,
                load_kvar, f, vtheta, angle
            )
            p_source_terminal = result["source_terminal"]["p_kw"]
            q_source_terminal = result["source_terminal"]["q_kvar"]

            # Vtheta BESS droop baseline is the hourly dispatch plan. The
            # slack-source residual provides the fixed-point correction:
            # Pcmd_new = Pcmd_old - Psource_terminal (absorbed-positive).
            p_target_raw = p_bess - p_source_terminal
            q_target_raw = q_bess - q_source_terminal
            p_target = self._soc_limited_p(p_target_raw, dt_hours)
            q_cap = min(
                c.pcs_q_kvar if c.pcs_q_kvar is not None else c.pcs_kva,
                math.sqrt(max(0.0, c.pcs_kva**2 - p_target**2)),
            )
            q_target = max(-q_cap, min(q_cap, q_target_raw))

            # Relax P/Q commands and obtain the corresponding GFM reference
            # from the inverse droop relation. The reference is not clamped to
            # a fixed value; physical bus voltage is checked after QSTS solve.
            p_new = p_bess + c.relaxation * (p_target - p_bess)
            q_new = q_bess + c.relaxation * (q_target - q_bess)
            soc_factor = max(self._soc, 0.05)
            if c.coupling == "X_gt_R":
                f_candidate = c.f_ref_hz - p_droop / soc_factor * (p_new - p_schedule)
                v_candidate = c.v_ref_pu - q_droop * (q_new - q_schedule)
            else:
                v_candidate = c.v_ref_pu - p_droop / soc_factor * (p_new - p_schedule)
                f_candidate = c.f_ref_hz - q_droop * (q_new - q_schedule)
            f_candidate = max(c.f_min_hz, min(c.f_max_hz, f_candidate))
            v_candidate = max(
                c.v_min_pu + c.vtheta_margin_pu,
                min(c.v_max_pu - c.vtheta_margin_pu, v_candidate),
            )
            f_new = f + c.relaxation * (f_candidate - f)
            v_new = 0.5 * (vtheta + v_candidate)

            # If a physical P/SOC limit prevents tracking the command, the
            # remaining deficit is EV unserved load; surplus is DER curtailment.
            p_saturated = abs(p_target_raw - p_target) > c.power_tolerance_kw
            if p_saturated and p_source_terminal < -c.slack_external_power_tolerance_kw:
                if not allow_ev_shedding:
                    raise RuntimeError("BESS P/SOC 饱和且离网供能不足，未允许 EV 负荷削减")
                shed = min(ev_served, -p_source_terminal)
                ev_served -= shed
                ev_shed += shed
            elif p_saturated and p_source_terminal > c.slack_external_power_tolerance_kw:
                remaining = p_source_terminal
                cut = min(pv_used, remaining)
                pv_used -= cut
                pv_curtailed += cut
                remaining -= cut
                cut = min(wind_used, remaining)
                wind_used -= cut
                wind_curtailed += cut

            q_saturated = abs(q_target_raw - q_target) > c.power_tolerance_kw
            if q_saturated and q_source_terminal < -c.power_tolerance_kw:
                if not allow_ev_shedding:
                    raise RuntimeError("BESS Q 饱和且无功不足，未允许 EV 负荷削减")
                q_load_per_kw = c.reactive_load_factor
                shed = min(ev_served, -q_source_terminal / max(q_load_per_kw, 1e-6))
                ev_served -= shed
                ev_shed += shed

            converged = (
                abs(p_source_terminal) <= c.slack_external_power_tolerance_kw
                and abs(q_source_terminal) <= c.power_tolerance_kw
                and abs(p_new - p_bess) <= c.power_tolerance_kw
                and abs(q_new - q_bess) <= c.power_tolerance_kw
                and abs(f_new - f) <= c.frequency_tolerance_hz
                and abs(v_new - vtheta) <= c.voltage_tolerance_pu
            )
            f, vtheta = f_new, v_new
            p_bess, q_bess = p_new, q_new
            if converged:
                break

        if not converged:
            raise RuntimeError(
                f"GFM QSTS 未收敛: iter={c.max_iterations}, "
                f"Psource={p_source_terminal:.3f} kW, "
                f"Qsource={q_source_terminal:.3f} kvar"
            )

        # Re-solve at the converged references and final BESS/served-load values.
        result = self._solve_network(
            pv_used, wind_used, ev_served, p_bess, q_bess, non_ev_kw, f, vtheta, angle
        )
        p_source_terminal = result["source_terminal"]["p_kw"]
        q_source_terminal = result["source_terminal"]["q_kvar"]
        if abs(p_source_terminal) > c.slack_external_power_tolerance_kw:
            raise RuntimeError(f"离网外部有功交换非零: {p_source_terminal:.3f} kW")
        if abs(q_source_terminal) > c.power_tolerance_kw:
            raise RuntimeError(f"离网外部无功交换非零: {q_source_terminal:.3f} kvar")

        voltage = result["voltage"]
        missing_ratings = [r["line"] for r in result["currents"] if not r["rating_valid"]]
        if missing_ratings and c.fail_on_unrated_lines:
            raise ValueError(f"以下线路未配置 NormAmps，无法校验热限值: {missing_ratings}")
        current_violation = [
            r for r in result["currents"]
            if (r["rating_valid"] and r["loading_pu"] is not None
                and r["loading_pu"] > c.line_current_limit_pu)
        ]
        if voltage["vmin_pu"] < c.v_min_pu - 1e-4 or voltage["vmax_pu"] > c.v_max_pu + 1e-4:
            raise RuntimeError(
                f"GFM QSTS 电压越限: [{voltage['vmin_pu']:.4f}, {voltage['vmax_pu']:.4f}] pu"
            )
        if current_violation:
            names = [r["line"] for r in current_violation]
            raise RuntimeError(f"GFM QSTS 线路电流越限: {names}")

        self._advance_soc(p_bess, dt_hours)
        self._frequency_hz = f
        self._vtheta_pu = vtheta
        self._vtheta_angle_deg = angle
        return {
            "converged": True,
            "iterations": iteration,
            "frequency_hz": f,
            "vtheta_pu": vtheta,
            "vtheta_angle_deg": angle,
            "p_bess_kw": p_bess,
            "q_bess_kvar": q_bess,
            "pv_used_kw": pv_used,
            "wind_used_kw": wind_used,
            "pv_curtailed_kw": pv_curtailed,
            "wind_curtailed_kw": wind_curtailed,
            "soc": self._soc,
            "ev_requested_kw": ev_requested,
            "ev_served_kw": ev_served,
            "ev_shed_kw": ev_shed,
            "vmin_pu": voltage["vmin_pu"],
            "vmax_pu": voltage["vmax_pu"],
            "line_currents": result["currents"],
            "losses_kw": result["losses_kw"],
            "residual_p_kw": p_source_terminal,
            "residual_q_kvar": q_source_terminal,
            "external_supply_kw": 0.0,
            "line_thermal_limits_verified": not bool(missing_ratings),
        }

    def solve_step(self, pv_kw, wind_kw, ev_base_kw, ev_load_kvar,
                   p_bess_schedule_kw=0.0, q_bess_schedule_kvar=0.0,
                   non_ev_kw=0.0, non_ev_kvar=0.0,
                   dt_hours=1.0, allow_ev_shedding=True):
        """执行 Cunha 风格 GFM 下垂外层迭代和 OpenDSS 快照潮流。

        OpenDSS Vsource 仅作 Storage_Bus 的数值 Vtheta 参考。BESS 命令正值
        表示向网络放电/注入。若 PCS/SOC 限值后仍有有功缺额，则按欠供功率
        削减 EV 负荷；若有盈余则依次限发 PV、风电。最终 Vsource P/Q 必须
        回到零交换容差内，否则该时步报不可行，不允许隐式外部补给。
        """
        c = self.config
        if dt_hours <= 0:
            raise ValueError("dt_hours 必须为正数")
        if c.pcs_kva is None:
            raise ValueError("GFM QSTS 需要 PCS 铭牌 pcs_kva")
        ev_requested = max(0.0, float(ev_base_kw))
        ev_served = ev_requested
        ev_kvar_requested = float(ev_load_kvar)
        pv_used = min(max(float(pv_kw), 0.0), c.pv_rated_kw)
        wind_used = min(max(float(wind_kw), 0.0), c.wind_rated_kw)
        p_schedule = self._soc_limited_p(float(p_bess_schedule_kw), dt_hours)
        q_schedule = float(q_bess_schedule_kvar)
        q_nameplate = c.pcs_q_kvar if c.pcs_q_kvar is not None else c.pcs_kva

        # Explicit outer fixed-point correction around the scheduled BESS P/Q.
        p_bess = p_schedule
        q_bess = q_schedule
        f = self._frequency_hz
        vtheta = self._vtheta_pu
        angle = self._vtheta_angle_deg
        p_slope, q_slope = self._slopes(c.pcs_p_kw)
        ev_shed = pv_curtailed = wind_curtailed = 0.0
        converged = False
        result = None

        for iteration in range(1, c.max_iterations + 1):
            ev_kvar_served = (ev_kvar_requested * ev_served / ev_requested
                              if ev_requested > 1e-9 else 0.0)
            result = self._solve_network(
                pv_used, wind_used, ev_served, p_bess, q_bess,
                non_ev_kw, ev_kvar_served, non_ev_kvar, f, vtheta, angle,
            )
            p_src = result["source_terminal"]["p_kw"]  # absorbed-positive
            q_src = result["source_terminal"]["q_kvar"]

            # A positive BESS command discharges. Increasing it reduces the
            # source's absorbed power, so the correction is schedule - source.
            p_raw = p_bess - p_src
            p_target = self._soc_limited_p(p_raw, dt_hours)
            q_raw = q_bess - q_src
            q_cap = min(q_nameplate, math.sqrt(max(0.0, c.pcs_kva**2 - p_target**2)))
            q_target = max(-q_cap, min(q_cap, q_raw))
            p_saturated = abs(p_raw - p_target) > c.power_tolerance_kw
            q_saturated = abs(q_raw - q_target) > c.power_tolerance_kw

            # If the requested BESS response is outside its actual feasible
            # set, classify the remainder instead of leaving it on Vsource.
            if p_saturated and p_src < -c.slack_external_power_tolerance_kw:
                if not allow_ev_shedding:
                    raise RuntimeError("BESS P/SOC 饱和且功率不足，未允许 EV 负荷削减")
                shed = min(ev_served, -p_src)
                ev_served -= shed
                ev_shed += shed
            elif p_saturated and p_src > c.slack_external_power_tolerance_kw:
                remaining = p_src
                cut = min(pv_used, remaining)
                pv_used -= cut
                pv_curtailed += cut
                remaining -= cut
                cut = min(wind_used, remaining)
                wind_used -= cut
                wind_curtailed += cut

            if q_saturated and q_src < -c.power_tolerance_kw:
                if not allow_ev_shedding:
                    raise RuntimeError("BESS Q 饱和且无功不足，未允许 EV 负荷削减")
                kvar_per_kw = abs(ev_kvar_requested) / max(ev_requested, 1e-6)
                shed = min(ev_served, -q_src / max(kvar_per_kw, 1e-6))
                ev_served -= shed
                ev_shed += shed

            p_new = p_bess + c.relaxation * (p_target - p_bess)
            q_new = q_bess + c.relaxation * (q_target - q_bess)

            # Inverse droop equations update the Vtheta/frequency references.
            soc_factor = max(self._soc, 0.05)
            if c.coupling == "X_gt_R":
                f_candidate = c.f_ref_hz - p_slope / soc_factor * (p_new - p_schedule)
                v_candidate = c.v_ref_pu - q_slope * (q_new - q_schedule)
            else:
                v_candidate = c.v_ref_pu - p_slope / soc_factor * (p_new - p_schedule)
                f_candidate = c.f_ref_hz - q_slope * (q_new - q_schedule)
            f_candidate = max(c.f_min_hz, min(c.f_max_hz, f_candidate))
            v_candidate = max(
                c.v_min_pu + c.vtheta_margin_pu,
                min(c.v_max_pu - c.vtheta_margin_pu, v_candidate),
            )
            f_new = f + c.relaxation * (f_candidate - f)
            v_new = 0.5 * (vtheta + v_candidate)

            converged = (
                abs(p_src) <= c.slack_external_power_tolerance_kw
                and abs(q_src) <= c.power_tolerance_kw
                and abs(p_new - p_bess) <= c.power_tolerance_kw
                and abs(q_new - q_bess) <= c.power_tolerance_kw
                and abs(f_new - f) <= c.frequency_tolerance_hz
                and abs(v_new - vtheta) <= c.voltage_tolerance_pu
            )
            p_bess, q_bess = p_new, q_new
            f, vtheta = f_new, v_new
            if converged:
                break

        if not converged:
            raise RuntimeError(
                f"GFM QSTS 未收敛: iter={c.max_iterations}, "
                f"Vsource P={p_src:.3f} kW, Q={q_src:.3f} kvar, "
                f"BESS P={p_bess:.3f} kW, Q={q_bess:.3f} kvar"
            )

        ev_kvar_served = (ev_kvar_requested * ev_served / ev_requested
                          if ev_requested > 1e-9 else 0.0)
        result = self._solve_network(
            pv_used, wind_used, ev_served, p_bess, q_bess,
            non_ev_kw, ev_kvar_served, non_ev_kvar, f, vtheta, angle,
        )
        p_src = result["source_terminal"]["p_kw"]
        q_src = result["source_terminal"]["q_kvar"]
        if abs(p_src) > c.slack_external_power_tolerance_kw:
            raise RuntimeError(f"Vtheta 数值参考仍有净有功交换: {p_src:.3f} kW")
        if abs(q_src) > c.power_tolerance_kw:
            raise RuntimeError(f"Vtheta 数值参考仍有净无功交换: {q_src:.3f} kvar")

        voltage = result["voltage"]
        if c.enforce_voltage_limits and (
            voltage["vmin_pu"] < c.v_min_pu - 1e-4
            or voltage["vmax_pu"] > c.v_max_pu + 1e-4
        ):
            raise RuntimeError(
                f"GFM QSTS 电压越限: [{voltage['vmin_pu']:.4f}, {voltage['vmax_pu']:.4f}] pu"
            )
        currents = result["currents"]
        missing_ratings = [r["line"] for r in currents if not r["rating_valid"]]
        if missing_ratings and c.fail_on_unrated_lines:
            raise ValueError(f"线路额定电流未配置，无法验证热约束: {missing_ratings}")
        over_limit = [
            r for r in currents if r["rating_valid"]
            and r["loading_pu"] is not None
            and r["loading_pu"] > c.line_current_limit_pu
        ]
        if over_limit:
            raise RuntimeError(f"线路电流越限: {[r['line'] for r in over_limit]}")

        self._advance_soc(p_bess, dt_hours)
        self._frequency_hz, self._vtheta_pu = f, vtheta
        return {
            "converged": True,
            "iterations": iteration,
            "frequency_hz": f,
            "vtheta_pu": vtheta,
            "vtheta_angle_deg": angle,
            "p_bess_kw": p_bess,
            "q_bess_kvar": q_bess,
            "pv_used_kw": pv_used,
            "wind_used_kw": wind_used,
            "pv_curtailed_kw": max(0.0, float(pv_kw) - pv_used) + pv_curtailed,
            "wind_curtailed_kw": max(0.0, float(wind_kw) - wind_used) + wind_curtailed,
            "soc": self._soc,
            "ev_requested_kw": ev_requested,
            "ev_served_kw": ev_served,
            "ev_shed_kw": ev_shed,
            "vmin_pu": voltage["vmin_pu"],
            "vmax_pu": voltage["vmax_pu"],
            "line_currents": currents,
            "losses_kw": result["losses_kw"],
            "residual_p_kw": p_src,
            "residual_q_kvar": q_src,
            "external_supply_kw": 0.0,
            "line_thermal_limits_verified": not bool(missing_ratings),
        }


# ---- 文本文件生成 (用于查看 / 备用) ---- #

def generate_dss_text(base_kv=0.4):
    """
    以编程方式生成主 DSS 文件文本内容。

    适用于查看或 dss-python 不可用时的备选方案。
    """
    return f"""Clear

Set DefaultBaseFrequency=50

New Circuit.ChargingStation_Microgrid bus1=MainBus basekV={base_kv} pu=1.0 phases=3

! 电压源作为系统参考 (平衡节点) - 由 New Circuit 命令自动创建
! (命名为 "source")。

! 线路参数: 0.4 kV 母线级连接 (低阻抗)
New LineCode.LV_Cable nphases=3 r1=0.01 x1=0.01 r0=0.01 x0=0.01 units=km

! 光伏支路 (额定 3000 kW)
New Line.PV_Line bus1=MainBus bus2=PV_Bus linecode=LV_Cable length=0.05 units=km
New Generator.PV1 bus1=PV_Bus phases=3 kV={base_kv} kW=0 model=1

! 风电支路 (额定 2000 kW)
New Line.Wind_Line bus1=MainBus bus2=Wind_Bus linecode=LV_Cable length=0.05 units=km
New Generator.Wind1 bus1=Wind_Bus phases=3 kV={base_kv} kW=0 model=1

! 充电桩负荷支路 (最大 14400 kW)
New Line.Load_Line bus1=MainBus bus2=Load_Bus linecode=LV_Cable length=0.05 units=km
New Load.EVLoad bus1=Load_Bus phases=3 kV={base_kv} kW=0 model=1

! 储能支路 (变流器 3750 kW, 容量 7500 kWh)
New Line.Storage_Line bus1=MainBus bus2=Storage_Bus linecode=LV_Cable length=0.05 units=km
New Storage.Storage1 bus1=Storage_Bus phases=3 kV={base_kv} kWrated=3750 kWhrated=7500 %stored=50 %reserve=10 %EffCharge=95 %EffDischarge=95 state=idling

Set Voltagebases=[{base_kv}]
CalcVoltageBases
"""
