import random
import re
import string
import datetime as dt
import matplotlib.pyplot as plt
import panel as pn
import hvplot.xarray
import xarray as xr
from tempfile import NamedTemporaryFile
import shutil
import os
from pathlib import Path
from yaml import dump

from swarmpal.io import PalDataItem, create_paldata
from swarmpal.experimental import LocalForwardMagneticModel
from swarmpal.toolboxes.fac.processes import FAC_single_sat
from swarmpal.utils.configs import SPACECRAFT_TO_MAGLR_DATASET

from common import HEADER, JINJA2_ENVIRONMENT, CustomisedFileDropper

pn.extension('filedropper')
xr.set_options(display_expand_groups=True, display_expand_attrs=True, display_expand_data_vars=True, display_expand_coords=True)

FAC_SINGLE_SAT_CODE_TEMPLATE = "fac-single-sat.jinja2"
FAC_SINGLE_SAT_CLI_TEMPLATE = "fac-single-sat-cli.jinja2"

start_of_today = dt.datetime.now().date()
end_of_today = start_of_today + dt.timedelta(days=1)
four_weeks_ago = end_of_today - dt.timedelta(days=28)

widgets = {
    "spacecraft": pn.widgets.RadioBoxGroup(options=list(SPACECRAFT_TO_MAGLR_DATASET.keys()), value="Swarm-A"),
    "grade": pn.widgets.RadioBoxGroup(options=["OPER", "FAST"], value="FAST"),
    "start-end": pn.widgets.DatetimeRangePicker(
        start=dt.date(2000, 1, 1),
        end=end_of_today,
        value=(start_of_today, end_of_today),
        enable_time=False,
    ),
    "file-dropper": CustomisedFileDropper(multiple=False),
    "evaluate-button": pn.widgets.Button(name="Click to evaluate", button_type="primary"),
}


class FacDataExplorer:
    def __init__(self, widgets):
        self.widgets = widgets
        self.cdf_download = pn.widgets.FileDownload(button_type="success")
        self.interactive_output = pn.pane.HoloViews()
        self.swarmpal_quicklook = pn.pane.Matplotlib()
        self.code_snippet = pn.pane.Markdown(styles={"font-size": "15px",})
        self.cli_command = pn.pane.Markdown(styles={"font-size": "15px",})
        self.output_title = pn.pane.Markdown(styles={"font-size": "20px",})
        self.data_view = pn.pane.HTML()
        self.output_pane = pn.Column(
            self.output_title,
            pn.layout.Divider(),
            self.cdf_download,
            pn.layout.Divider(),
            pn.Tabs(
                ("SwarmPAL quicklook", self.swarmpal_quicklook),
                ("Interactive view", self.interactive_output),
                ("Data view", self.data_view),
                ("SwarmPAL Python Code", self.code_snippet),
                ("SwarmPAL CLI Command", self.cli_command),
            ),
        )
        self.widgets["evaluate-button"].on_click(self.update_data)
        self.widgets["file-dropper"].param.watch(self.update_data_local, "value")
        # self.update_data(None)

    @property
    def controls(self):
        vires_widgets = pn.Column(
            pn.pane.Markdown("Select duration:"),
            self.widgets["start-end"],
            pn.layout.Divider(),
            pn.pane.Markdown("Select spacecraft:"),
            self.widgets["spacecraft"],
            pn.layout.Divider(),
            pn.pane.Markdown("Select processing chain: (FAST only available for Swarm)"),
            self.widgets["grade"],
            pn.layout.Divider(),
            self.widgets["evaluate-button"],
        )
        local_file_widgets = pn.Column(
            pn.pane.Markdown("Upload CDF file:"),
            self.widgets["file-dropper"],
            pn.layout.Divider(),
        )
        return pn.Column(
            pn.Tabs(
                ("VirES (remote)", vires_widgets),
                ("CDF File", local_file_widgets),
            ),
        )

    @property
    def time_start_end_str(self):
        t_s, t_e = self.widgets["start-end"].value
        return f'{t_s.strftime("%Y%m%dT%H%M%S")}_{t_e.strftime("%Y%m%dT%H%M%S")}'

    @property
    def spacecraft(self):
        return self.widgets["spacecraft"].value

    @property
    def grade(self):
        return self.widgets["grade"].value
    
    @property
    def mode(self):
        try:
            return self._mode
        except AttributeError:
            # Set default mode
            self.set_mode()
            return self._mode

    def set_mode(self, mode="vires"):
        """Set the mode to either 'vires' or 'local'"""
        if mode not in ["vires", "local"]:
            raise ValueError("Mode must be either 'vires' or 'local'")
        self._mode = mode
    
    @property
    def data_params(self):
        """Parameters to pass to swarmpal to fetch the inputs"""
        try:
            return self._data_params
        except AttributeError:
            # Set default data parameters
            self.set_data_params()
            return self._data_params
    
    def set_data_params(self, mode="vires", filename=None):
        """Set parameters to pass to swarmpal to fetch the inputs"""
        if mode == "vires":
            collection = SPACECRAFT_TO_MAGLR_DATASET[self.spacecraft]
            if self.grade == "FAST":
                # Reset to OPER if the spacecraft is not Swarm (not applicable)
                if "Swarm" not in self.spacecraft:
                    self.widgets["grade"].value = "OPER"
                else:
                    collection = collection.replace("OPER", "FAST")
            if "Swarm" in self.spacecraft:
                measurements = ["B_NEC", "Flags_F", "Flags_B", "Flags_q"]
            else:
                measurements = ["B_NEC"]
            self._data_params = dict(
                collection=collection,
                measurements=measurements,
                models=["CHAOS"],
                start_time=self.widgets["start-end"].value[0].isoformat(),
                end_time=self.widgets["start-end"].value[1].isoformat(),
                server_url="https://vires.services/ows",
                options=dict(asynchronous=False, show_progress=False),
            )
        elif mode == "local":
            self._data_params = dict(
                filename=filename,
                filetype="cdf",
            )
    
    @property
    def process_params(self):
        try:
            return self._process_params
        except AttributeError:
            # Set default process parameters
            self.set_process_params()
            return self._process_params
    
    def set_process_params(self, mode="vires", dataset=None):
        if mode == "vires":
            time_jump_limit = 1 if "Swarm" in self.spacecraft else 10
            self._process_params = dict(
                dataset=self.data_params["collection"],
                model_varname="B_NEC_CHAOS",
                measurement_varname="B_NEC",
                time_jump_limit=time_jump_limit,
            )
        elif mode == "local":
            self._process_params = dict(
                dataset=dataset,
                model_varname="B_NEC_CHAOS-Core",
                measurement_varname="B_NEC",
                time_jump_limit=1,
            )

    def update_data(self, event):
        """Fetch and process the data"""
        self.set_mode("vires")
        self.set_data_params(mode="vires")
        self.data = create_paldata(
            PalDataItem.from_vires(**self.data_params)
        )
        self.set_process_params(mode="vires")
        process = FAC_single_sat(
            config=self.process_params
        )
        self.data = process(self.data)
        title = f"""
        {self.widgets["spacecraft"].value} {self.widgets["grade"].value}: FAC single-satellite method
        
        {self.widgets["start-end"].value[0]} to {self.widgets["start-end"].value[1]}
        """
        self.update_output_pane(title)
        self.update_output_file(f'SwarmPAL_FAC_{self.spacecraft}_{self.grade}_{self.time_start_end_str}.cdf')
    
    def update_data_local(self, event):
        """Fetch and process the data"""
        self.set_mode("local")
        # Identify file name and set product name from that
        filename = self.widgets["file-dropper"].file_in_mem.name
        self.set_data_params(mode="local", filename=filename)
        product_name_full = Path(filename).stem
        # Truncate to remove data and version
        product_name = re.sub(r"_\d{8}T\d{6}.*$", "", product_name_full)
        # Load the CDF file into a SwarmPAL DataTree
        self.data = create_paldata(
            **{product_name: PalDataItem.from_file(self.widgets["file-dropper"].temp_file.name, filetype="cdf")}
        )
        # Evaluate the field model locally
        process_local_model = LocalForwardMagneticModel()
        process_local_model.set_config(
            dataset=product_name,
            model_descriptor="CHAOS-Core",
        )
        self.data = process_local_model(self.data)
        # Apply the FAC single-satellite process
        self.set_process_params(mode="local", dataset=product_name)
        process = FAC_single_sat(
            config=self.process_params
        )
        self.data = process(self.data)
        title = f"""
        {self.widgets["file-dropper"].file_in_mem.name}

        Applied local model: CHAOS-Core, and FAC single-satellite method
        """
        self.update_output_pane(title)
        self.update_output_file(f'SwarmPAL_FAC_{product_name_full}.cdf')

    def update_output_pane(self, title="SwarmPAL FAC"):
        """Update all output panes"""
        self.output_title.object = title
        # Interactive HoloViews plot
        if "Flags_F" in self.data["PAL_FAC_single_sat"].data_vars:
            mask_valid_F = self.data["PAL_FAC_single_sat"]["Flags_F"] <= 1
            mask_valid_B = self.data["PAL_FAC_single_sat"]["Flags_B"] <= 1
            mask_valid = mask_valid_F & mask_valid_B
            masked_data = self.data["PAL_FAC_single_sat"].to_dataset().where(mask_valid, drop=True)
            hvplot_obj = masked_data.hvplot(x="Timestamp", y="FAC", ylim=(-30, 30))
        else:
            hvplot_obj = self.data["PAL_FAC_single_sat"].to_dataset().hvplot(x="Timestamp", y="FAC", ylim=(-30, 30))
        self.interactive_output.object = hvplot_obj
        # SwarmPAL quicklook
        try:
            fig, _ = self.data.swarmpal_fac.quicklook()
            self.swarmpal_quicklook.object = fig
        except Exception:
            fig = self._empty_matplotlib_figure()
            self.swarmpal_quicklook.object = fig
        # Code snippet
        self.code_snippet.object = f"```python\n{self.get_code()}\n```"
        # CLI example
        self.cli_command.object = self.get_cli()
        # Data view
        self.data_view.object = self.data._repr_html_()

    @staticmethod
    def _empty_matplotlib_figure():
        fig, ax = plt.subplots()
        ax.set_axis_off()
        ax.text(0.5, 0.5, "No data available / error in figure creation", ha="center", va="center", fontsize=20)
        return fig

    def get_cdf_file(self):
        # work around the weirdness of cdflib xarray tools by writing to another file first then moving to a temporary file
        deleteme ="/tmp/tmp" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(10)) + ".cdf"
        self.data.swarmpal.to_cdf(deleteme, leaf="PAL_FAC_single_sat")
        # Create the tempfile as a an object property so it doesn't go out of scope and get deleted
        # It will automatically be replaced (and old file removed) each time this is run
        self.tempfile_cdf = NamedTemporaryFile()
        with open(deleteme, "rb") as src_file:
            shutil.copyfileobj(src_file, self.tempfile_cdf)
            self.tempfile_cdf.seek(0)
        os.remove(deleteme)
        return self.tempfile_cdf

    def update_output_file(self, filename="SwarmPAL_FAC.cdf"):
        self.cdf_download.file = self.get_cdf_file().name
        self.cdf_download.filename = filename
    
    def get_code(self):
        """
        Get the code to reproduce the current plot.
        """
        data_params = self.data_params
        process_params = self.process_params
        if self.mode == "vires":
            context = {
                "mode": "vires",
                "collection": data_params["collection"],
                "measurements": data_params["measurements"],
                "models": data_params["models"],
                "start_time": data_params["start_time"],
                "end_time": data_params["end_time"],
                "server_url": data_params["server_url"],
                "asynchronous": data_params["options"]["asynchronous"],
                "show_progress": data_params["options"]["show_progress"],
                "dataset": process_params["dataset"],
                "model_varname": process_params["model_varname"],
                "measurement_varname": process_params["measurement_varname"],
                "time_jump_limit": process_params["time_jump_limit"],
            }
        elif self.mode == "local":
            context = {
                "mode": "local",
                "filename": data_params["filename"],
                "filetype": data_params["filetype"],
                "dataset": process_params["dataset"],
                "model_varname": process_params["model_varname"],
                "measurement_varname": process_params["measurement_varname"],
                "time_jump_limit": process_params["time_jump_limit"],
            }
        template = JINJA2_ENVIRONMENT.get_template(FAC_SINGLE_SAT_CODE_TEMPLATE)
        return template.render(context)

    def get_cli(self):
        """
        Get the YAML configuration and CLI command to download and process the data for the current plot.
        """
        process_params = []
        if self.mode == 'vires':
            data_params = {'provider': 'vires', **self.data_params}
            data_params.pop('options')

        elif self.mode == 'local':
            dataset = self.process_params['dataset']
            data_params = {'provider': 'file', 'dataset': dataset, **self.data_params}
            process_params.append(dict(
                process_name="EXP_LocalForwardMagneticModel",
                dataset=dataset,
                model_descriptor="CHAOS-Core",
            ))

        process_params.append({'process_name': 'FAC_single_sat', **self.process_params})


        config = dict(
            data_params=[data_params],
            process_params=process_params,
        )
        config_yaml = dump(config, sort_keys=False)
        context = dict(
            config=config_yaml,
        )
        template = JINJA2_ENVIRONMENT.get_template(FAC_SINGLE_SAT_CLI_TEMPLATE)
        return template.render(context)

data_explorer = FacDataExplorer(widgets)

dashboard = pn.template.BootstrapTemplate(
    header=HEADER,
    title="SwarmPAL: FAC",
    sidebar=data_explorer.controls,
    main=data_explorer.output_pane,
).servable()


if __name__ == "__main__":
    dashboard.show()
