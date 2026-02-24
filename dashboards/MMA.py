import datetime as dt
import os
from pathlib import Path
import random
import re
import shutil
import string
from tempfile import NamedTemporaryFile

import cartopy.crs as ccrs
import hvplot.xarray
import matplotlib.pyplot as plt
import panel as pn
import xarray as xr

from swarmpal_mma.pal_processes import MMA_SHA_2E
from swarmpal_mma.Plotting.map_plot import map_surface_rtp
from swarmpal.experimental import LocalForwardMagneticModel
from swarmpal.io import PalDataItem, create_paldata
from swarmpal.utils.configs import SPACECRAFT_TO_MAGLR_DATASET

from common import HEADER, JINJA2_ENVIRONMENT, CustomisedFileDropper

pn.extension('filedropper')
xr.set_options(display_expand_groups=True, display_expand_attrs=True, display_expand_data_vars=True, display_expand_coords=True)

MMA_2E_CODE_TEMPLATE = "mma-2e.jinja2"

start_of_today = dt.datetime.now().date()
end_of_today = start_of_today + dt.timedelta(days=1)
four_weeks_ago = end_of_today - dt.timedelta(days=28)
default_start = dt.datetime(2026, 1, 1)
default_end = dt.datetime(2026, 1, 2)

widgets = {
    "spacecraft": pn.widgets.CheckBoxGroup(
        options=list(SPACECRAFT_TO_MAGLR_DATASET.keys()), value=["Swarm-A", "Swarm-B"]
    ),
    # "grade": pn.widgets.RadioBoxGroup(options=["OPER", "FAST"], value="FAST"),
    "start-end": pn.widgets.DatetimeRangePicker(
        # start=four_weeks_ago,
        start=dt.date(2000, 1, 1),
        end=end_of_today,
        value=(default_start, default_end),
        enable_time=False,
    ),
    "file-dropper": CustomisedFileDropper(multiple=False),
    "button-fetch-data": pn.widgets.Button(name="Fetch inputs", button_type="primary"),
    "button-run-analysis": pn.widgets.Button(
        name="Run analysis", button_type="primary"
    ),
}


class MmaDataExplorer:
    def __init__(self, widgets):
        self.widgets = widgets
        self.cdf_download = pn.widgets.FileDownload(button_type="success")
        self.interactive_output = pn.pane.HoloViews()
        self.swarmpal_quicklook = pn.pane.Matplotlib()
        self.code_snippet = pn.pane.Markdown(styles={"font-size": "15px",})
        self.data_view = pn.pane.HTML()
        self.output_title = pn.pane.Markdown()
        self.output_pane = pn.Column(
            self.output_title,
            pn.layout.Divider(),
            # self.cdf_download,
            # pn.layout.Divider(),
            pn.Tabs(
                ("Data view", self.data_view),
                ("SwarmPAL quicklook", self.swarmpal_quicklook),
                # ("Interactive plot", self.interactive_output),
                ("Code snippet", self.code_snippet),
            ),
        )
        self.widgets["button-fetch-data"].on_click(self.update_input_data)
        # self.widgets["file-dropper"].param.watch(self.update_input_data, "value")
        self.widgets["button-run-analysis"].on_click(self.update_analysis)
        # self.update_data(None)

    @property
    def controls(self):
        return pn.Column(
            pn.pane.Markdown("Select duration:"),
            widgets["start-end"],
            pn.layout.Divider(),
            pn.pane.Markdown("Select spacecraft:"),
            widgets["spacecraft"],
            pn.layout.Divider(),
            # pn.pane.Markdown("Select processing chain: (FAST only available for Swarm)"),
            # widgets["grade"],
            self.widgets["file-dropper"],
            widgets["button-fetch-data"],
            widgets["button-run-analysis"],
        )

    @property
    def time_start_end_str(self):
        t_s, t_e = self.widgets["start-end"].value
        return f"{t_s.strftime('%Y%m%dT%H%M%S')}_{t_e.strftime('%Y%m%dT%H%M%S')}"

    @property
    def spacecraft(self):
        return self.widgets["spacecraft"].value

    @property
    def grade(self):
        return self.widgets["grade"].value

    def get_data_config(self):
        """Parameters to pass to swarmpal to fetch the inputs"""
        collections = [SPACECRAFT_TO_MAGLR_DATASET.get(sc) for sc in self.spacecraft]
        data_config = {}
        for collection in collections:
            data_config[collection] = dict(
                collection=collection,
                measurements=["B_NEC"],
                models=['CHAOS-Core'],
                sampling_step="PT25S",
                start_time=self.widgets["start-end"].value[0].isoformat(),
                end_time=self.widgets["start-end"].value[1].isoformat(),
                server_url="https://vires.services/ows",
                options=dict(asynchronous=False, show_progress=False),
            )
        return data_config
    
    def load_local_data(self) -> tuple[str, PalDataItem] | None:
        """Load local data from a file"""
        if self.widgets["file-dropper"].value:
            # Identify file name and set product name from that
            filename = self.widgets["file-dropper"].file_in_mem.name
            product_name_full = Path(filename).stem
            # Truncate to remove data and version
            product_name = re.sub(r"_\d{8}T\d{6}.*$", "", product_name_full)
            # Load the file
            pdi = PalDataItem.from_file(self.widgets["file-dropper"].temp_file.name, filetype="cdf")
            return product_name, pdi
        else:
            return None

    def fetch_data(self):
        data = create_paldata(
            **{
                label: PalDataItem.from_vires(**data_params)
                for label, data_params in self.get_data_config().items()
            }
        )
        if self.widgets["file-dropper"].value:
            # If a file is uploaded, read it and add it to the data
            product_name, pdi = self.load_local_data()
            data[product_name] = pdi.xarray
            # (HACK) Subset the data to match the PT25S data cadence
            data[product_name] = data[product_name].sel(Timestamp=data[product_name].ds["Timestamp"][::25])
            # Evaluate the CHAOS model locally
            process_local_model = LocalForwardMagneticModel()
            process_local_model.set_config(
                dataset=product_name,
                model_descriptor="CHAOS-Core",
            )
            process_local_model(data)
        return data

    @staticmethod
    def _run_mma_2e_code(data):
        mma_process = MMA_SHA_2E()
        mma_process.set_config(
            measurement_varname="B_NEC",
            model_varname="B_NEC_CHAOS-Core",
        )
        data = mma_process(data)
        return data

    @staticmethod
    def _quicklook(data):
        ds = data["MMA_SHA_2E"].ds
        fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(8, 7))
        axes[0].plot(ds["time"], ds["qs"][:, 0], label="q^1_0")
        axes[0].plot(ds["time"], ds["gh"][:, 0], label="g^1_0")
        axes[0].set_xlabel("MJD2000")
        axes[0].legend()
        # Globe plot
        axes[1] = fig.add_subplot(2, 1, 2, projection=ccrs.EqualEarth(180))
        map_surface_rtp(ds["qs"][0, :], fig=fig, ax=axes[1])
        # fig = map_surface_rtp(ds["qs"][0, :])
        # axes = None
        return fig, axes

    def update_input_data(self, event):
        self.data_view.object = ""
        self.swarmpal_quicklook.object = self._pending_matplotlib_figure()
        self.data = self.fetch_data()
        # self.data_view.object = self.data  # when html repr is fixed
        raw_string = self.data.__str__()
        html_string = raw_string.replace("\n", "<br>")
        self.data_view.object = f"<pre>{html_string}</pre>"
        self.code_snippet.object = f"```python\n{self.get_code()}\n```"

    def update_analysis(self, event):
        self.data = self._run_mma_2e_code(self.data)
        # self.data_view.object = self.data  # when html repr is fixed
        raw_string = self.data.__str__()
        html_string = raw_string.replace("\n", "<br>")
        self.data_view.object = f"<pre>{html_string}</pre>"
        self._update_output_pane()
        # self._update_cdf_file()

    def _update_output_pane(self):
        # title = f"## {self.widgets['spacecraft'].value} \n{self.widgets['start-end'].value[0]} to {self.widgets['start-end'].value[1]}"
        title = "## MMA_SHA_2E"
        inputs = ', '.join([s.strip("/") for s in self.data.groups[1:-1]])
        title += f"\n\nInputs: {inputs}"
        self.output_title.object = title
        # Interactive HoloViews plot
        self.interactive_output.object = None
        # hvplot_obj = self.data["MMA_SHA_2E"].ds.hvplot.explore()
        # self.interactive_output.object = hvplot_obj
        # SwarmPAL quicklook
        fig, _ = self._quicklook(self.data)
        self.swarmpal_quicklook.object = fig
        try:
            fig, _ = self._quicklook(self.data)
            self.swarmpal_quicklook.object = fig
        except Exception:
            fig = self._empty_matplotlib_figure()
            self.swarmpal_quicklook.object = fig

    @staticmethod
    def _empty_matplotlib_figure():
        fig, ax = plt.subplots()
        ax.set_axis_off()
        ax.text(
            0.5,
            0.5,
            "No data available / error in figure creation",
            ha="center",
            va="center",
            fontsize=20,
        )
        return fig

    @staticmethod
    def _pending_matplotlib_figure():
        fig, ax = plt.subplots()
        ax.set_axis_off()
        ax.text(0.5, 0.5, "Analysis not yet run", ha="center", va="center", fontsize=20)
        return fig

    def get_cdf_file(self):
        # work around the weirdness of cdflib xarray tools by writing to another file first then moving to a temporary file
        deleteme = (
            "/tmp/tmp"
            + "".join(
                random.choice(string.ascii_letters + string.digits) for _ in range(10)
            )
            + ".cdf"
        )
        self.data.swarmpal.to_cdf(deleteme, leaf="PAL_FAC_single_sat")
        # Create the tempfile as a an object property so it doesn't go out of scope and get deleted
        # It will automatically be replaced (and old file removed) each time this is run
        self.tempfile_cdf = NamedTemporaryFile()
        with open(deleteme, "rb") as src_file:
            shutil.copyfileobj(src_file, self.tempfile_cdf)
            self.tempfile_cdf.seek(0)
        os.remove(deleteme)
        return self.tempfile_cdf

    def _update_cdf_file(self):
        self.cdf_download.file = self.get_cdf_file().name
        self.cdf_download.filename = (
            f"SwarmPAL_FAC_{self.spacecraft}_{self.grade}_{self.time_start_end_str}.cdf"
        )

    def get_code(self):
        data_config = self.get_data_config()
        context = {"data_config": data_config}
        template = JINJA2_ENVIRONMENT.get_template(MMA_2E_CODE_TEMPLATE)
        return template.render(context)

data_explorer = MmaDataExplorer(widgets)

dashboard = pn.template.BootstrapTemplate(
    header=HEADER,
    title="SwarmPAL dashboard: MMA",
    sidebar=data_explorer.controls,
    main=data_explorer.output_pane,
).servable()


if __name__ == "__main__":
    dashboard.show()
