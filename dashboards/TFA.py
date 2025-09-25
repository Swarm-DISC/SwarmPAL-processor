import datetime as dt
import matplotlib.pyplot as plt
import panel as pn
import pprint
from bokeh.models.formatters import PrintfTickFormatter
import swarmpal
from swarmpal.utils.configs import SPACECRAFT_TO_MAGLR_DATASET
from yaml import dump

from common import HEADER, JINJA2_ENVIRONMENT, CustomisedFileDropper

TFA_VIRES_CODE_TEMPLATE = "tfa_vires.jinja2"
TFA_CDF_CODE_TEMPLATE = "tfa_cdf.jinja2"
FAC_SINGLE_SAT_CLI_TEMPLATE = "fac-single-sat-cli.jinja2"

# TODO: Some of the date picker functionality is copied from the FAC dashboard and can be moved to common.py
start_of_today = dt.datetime.now().date()
end_of_today = start_of_today + dt.timedelta(days=1)
two_days_ago = start_of_today - dt.timedelta(days=2)
yesterday = start_of_today - dt.timedelta(days=1)
four_weeks_ago = end_of_today - dt.timedelta(days=28)

widgets = {
    # For dataset params
    "spacecraft": pn.widgets.RadioBoxGroup(options=['Swarm-A', 'Swarm-B', 'Swarm-C'], value='Swarm-A'),
    "start-end": pn.widgets.DatetimeRangePicker(
        start=dt.date(2000, 1, 1),
        end=end_of_today,
        value=(start_of_today - dt.timedelta(days=4), start_of_today - dt.timedelta(days=3)),
        enable_time=False,
    ),
    "update-button": pn.widgets.Button(name="Update", button_type="primary"),
    "evaluate-button": pn.widgets.Button(name="Click to evaluate", button_type="primary"),
    "file-dropper": CustomisedFileDropper(multiple=False),
    # For TFA_Preprocess params
    "preprocess-active-component": pn.widgets.DiscreteSlider(
        name="Active Component", 
        options=[0, 1,2], 
        value=2,
    ),
    "preprocess-sampling-rate": pn.widgets.EditableFloatSlider(
        name="Sampling Rate", 
        format=PrintfTickFormatter(format='%.0f Hz'), 
        value=1.0,
        step=1.0,
    ),
    # For TFA_Clean params
    "clean-window-size": pn.widgets.EditableIntSlider(
        name="Window Size",
        start=100,
        end=1000,
        step=100,
        value=300,
    ),
    "clean-multiplier": pn.widgets.EditableFloatSlider(
        name="Multiplier", 
        format=PrintfTickFormatter(format='%.1f'), 
        value=0.5,
        step=0.1,
        start=0.1,
        end=2.0,
    ),
    "clean-method": pn.widgets.Select(
        name='Method', 
        options=['iqr', 'normal'],
        value='iqr',
    ),
    # For TFA_Filter params
    "filter-cutoff": pn.widgets.EditableFloatSlider(
        name="Cut off frequency", 
        format=PrintfTickFormatter(format='%.03f Hz'), 
        value=0.02,
        step=0.001,
        start=0.001,
        end=0.2,
    ),
    # For TFA_Wavelet params
    "wavelet-min-frequency" : pn.widgets.EditableFloatSlider(
        name="Minimum frequency", 
        format=PrintfTickFormatter(format='%.03f Hz'), 
        value=0.02,
        step=0.001,
        start=0.0,
        end=0.2,
    ),
    "wavelet-max-frequency" : pn.widgets.EditableFloatSlider(
        name="Maximum frequency", 
        format=PrintfTickFormatter(format='%.03f Hz'), 
        value=0.1,
        step=0.001,
        start=0.0,
        end=0.2,
    ),
    "wavelet-dj" : pn.widgets.EditableFloatSlider(
        name="DJ", 
        format=PrintfTickFormatter(format='%.02f'), 
        value=0.1,
        step=0.01,
        start=0.0,
        end=1.0,
    ),
}



def pprinter(object):
    '''Helper function to pretty print nested dicts and lists.

    Similar Python's built in pprint module, but uses the 'dict' constructor
    for dictionaries instead of curly brace syntax.
    '''
    def _newline(indent):
        return '\n' + (' ' * indent)
    def _pprinter(obj, indent):
        result = ''
        if isinstance(obj, dict):
            result += 'dict(' #)
            for item in obj:
                result += _newline(indent+4) + item + '='
                result += _pprinter(obj[item], indent+4)
                result += ","
            return result + _newline(indent) + ")"
        if isinstance(obj, list):
            result += '[' #]
            for item in obj:
                result += _newline(indent+4)
                result += _pprinter(item, indent+4)
                result += ','
            return result + _newline(indent) + ']'
        return repr(obj)

    return _pprinter(object, 0)

class TFA_GUI:
    def __init__(self, widgets):
        self.widgets = widgets

        self.output_title = pn.pane.Markdown()
        self.swarmpal_quicklook = pn.pane.Matplotlib()
        self.data_view = pn.pane.HTML()
        self.code_snippet = pn.pane.Markdown(styles={"font-size": "15px",})
        self.cli_command = pn.pane.Markdown(styles={"font-size": "15px",})

        self.widgets["update-button"].on_click(self.update_output_pane)
        self.widgets["evaluate-button"].on_click(self.update_data)
        self.data = None
        self.data_tabs = None

    @property
    def sidebar(self):
        '''Panel UI definition for the sidebar.'''
        self.data_tabs = pn.Tabs(
            ("VirES (remote", pn.Column(
                pn.pane.Markdown("Select Duration"),
                self.widgets["start-end"],
                pn.pane.Markdown("Select spacecraft"),
                self.widgets["spacecraft"],
                )),
             ("CDF File", pn.Column(
                 pn.pane.Markdown("Upload CDF file:"),
                 self.widgets["file-dropper"],
                 )),
        )
        return pn.Column(
            pn.pane.Markdown("## Data Parameters"),
            self.data_tabs,
            pn.layout.Divider(),
            pn.pane.Markdown("## Process Parameters"),
            pn.pane.Markdown("### Preprocess"),
            self.widgets['preprocess-active-component'],
            self.widgets['preprocess-sampling-rate'],
            pn.layout.Divider(),
            pn.pane.Markdown("### Clean"),
            self.widgets['clean-method'],
            self.widgets['clean-window-size'],
            self.widgets['clean-multiplier'],
            pn.layout.Divider(),
            pn.pane.Markdown("### Filter"),
            self.widgets['filter-cutoff'],
            pn.layout.Divider(),
            pn.pane.Markdown("### Wavelet"),
            self.widgets['wavelet-min-frequency'],
            self.widgets['wavelet-max-frequency'],
            self.widgets['wavelet-dj'],
            pn.layout.Divider(),
            self.widgets["update-button"],
            self.widgets["evaluate-button"],
        )

    @property
    def main(self):
        return pn.Column(
            self.output_title,
            pn.Tabs(
                ("SwarmPal quicklook", self.swarmpal_quicklook),
                ("Data view", self.data_view),
                ("SwarmPAL Python Code", self.code_snippet),
                ("SwarmPAL CLI Command", self.cli_command),
            ),
        )

    def _get_data_product(self, spacecraft):
        '''Translates the spacecraft radio group to a Swarm data product.'''
        if spacecraft == 'Swarm-A':
            return "SW_OPER_MAGA_LR_1B"
        if spacecraft == 'Swarm-B':
            return "SW_OPER_MAGB_LR_1B"
        if spacecraft == 'Swarm-C':
            return "SW_OPER_MAGC_LR_1B"
        return "SW_OPER_MAGA_LR_1B"
    
    def _make_vires_data_params(self):
        data_product = self._get_data_product(self.widgets['spacecraft'].value)
        return [dict(
            provider="vires",
            collection=data_product,
            measurements=["B_NEC"],
            models=["Model='CHAOS-Core'+'CHAOS-Static'"],
            auxiliaries=["QDLat", "MLT"],
            start_time=self.widgets["start-end"].value[0].isoformat(),
            end_time=self.widgets["start-end"].value[1].isoformat(),
            pad_times=["03:00:00", "03:00:00"],
            server_url="https://vires.services/ows",
        )]

    def _make_cdf_data_params(self):
        filename = self.widgets["file-dropper"].file_in_mem.name
        return [dict(
            provider="file",
            filename=filename,
            filetype="cdf",
        )]

    def _using_cdf_input(self):
        return self.data_tabs.active == 1

    def _make_data_params(self):

        if self._using_cdf_input():
            return self._make_cdf_data_params()
        return self._make_vires_data_params()

    def make_config(self):
        '''Create a schema compatible data structure that describes the input dataset and SwarmPAL processes.'''
        data_product = self._get_data_product(self.widgets['spacecraft'].value)
        data_params = self._make_data_params()
        process_params = [
            dict(
                process_name="TFA_Preprocess",
                dataset=data_product,
                active_variable="B_NEC_res_Model",
                active_component=self.widgets['preprocess-active-component'].value,
                sampling_rate=self.widgets['preprocess-sampling-rate'].value,
                remove_model=True,
            ),
            dict(
                process_name="TFA_Clean",
                window_size=self.widgets['clean-window-size'].value,
                method=self.widgets['clean-method'].value,
                multiplier=self.widgets['clean-multiplier'].value,
            ),
            dict(
                process_name="TFA_Filter",
                cutoff_frequency=self.widgets['filter-cutoff'].value,
            ),
            dict(
                process_name="TFA_Wavelet",
                min_frequency=self.widgets['wavelet-min-frequency'].value,
                max_frequency=self.widgets['wavelet-max-frequency'].value,
                dj=self.widgets['wavelet-dj'].value,
            ),
        ]
        return dict(
            data_params=data_params,
            process_params=process_params,
        )

    def update_data(self, event):
        '''Downloads input data, applies processes and updates the main pane'''

        if self._using_cdf_input() and not self.widgets["file-dropper"].value:
            return

        config = self.make_config()
        self.data = swarmpal.fetch_data(config)
        swarmpal.apply_processes(self.data, config['process_params'])
        self.update_output_pane(event)

    def update_output_pane(self, event, title="# SwarmPAL TFA Quicklook"):
        '''Update the mane pane'''

        self.output_title.object = title

        if self._using_cdf_input() and not self.widgets["file-dropper"].value:
            # TODO: ask user for a file
            return

        # TODO: generate CDF code
        if self._using_cdf_input():
            self.code_snippet.object = self.get_cdf_code()
            self.cli_command.object = None
        else:
            self.code_snippet.object = self.get_vires_code()
            self.cli_command.object = self.get_cli()

        if not self.data:
            return

        self.data_view.object = self.data._repr_html_()
        #fig, _ = self.data.swarmpal_
        fig, _ = swarmpal.toolboxes.tfa.plotting.quicklook(
            self.data,
            tlims=(
                self.widgets["start-end"].value[0].isoformat(),
                self.widgets["start-end"].value[1].isoformat(),
            ),
            extra_x=('QDLat', 'MLT', 'Latitude'),
        )
        #try:
        #except Exception as ex:
        #    print(ex)

        #    fig = self._empty_matplotlib_figure()
        self.swarmpal_quicklook.object = fig




    @staticmethod
    def _empty_matplotlib_figure():
        fig, ax = plt.subplots()
        ax.set_axis_off()
        ax.text(0.5, 0.5, "No data available / error in figure creation", ha="center", va="center", fontsize=20)
        return fig

    def get_vires_code(self):
        '''Updates the Python code snippet'''
        config = self.make_config()
        #config_code = pprint.pformat(config, sort_dicts=False)
        config_code = pprinter(config) #, sort_dicts=False)
        context = dict(
            config=config_code,
        )
        template = JINJA2_ENVIRONMENT.get_template(TFA_VIRES_CODE_TEMPLATE)
        return f"```python\n{template.render(context)}\n```"
        #return f"```python\nprint('hello world')\n```"

    def get_cdf_code(self):
        pass

    def get_cli(self):
        '''Updates the CLI example snippet'''
        config = self.make_config()
        config_yaml = dump(config, sort_keys=False)
        context = dict(
            config=config_yaml,
        )
        template = JINJA2_ENVIRONMENT.get_template(FAC_SINGLE_SAT_CLI_TEMPLATE)
        return template.render(context)


tfa_gui = TFA_GUI(widgets)

dashboard = pn.template.BootstrapTemplate(
    header=HEADER,
    title="SwarmPAL: TFA",
    sidebar=tfa_gui.sidebar,
    main=tfa_gui.main,
).servable()

if __name__ == '__main__':
    dashboard.show()
