"""
基于 dss-python 的 OpenDSS 微电网模型接口。

提供编译 DSS 模型、设置各时间步长组件功率、求解潮流
以及提取仿真结果的方法。
需要 dss-python (pip install dss-python)。
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

    @property
    def is_ready(self):
        return self._compiled

    def compile(self, dss_file="model/master.dss"):
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
