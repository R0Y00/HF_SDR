using System.Windows.Forms;
using SDRSharp.Common;
using SDRSharp.Radio;

namespace SDRSharp.HFSDR
{
    public sealed class HFSdrPlugin : ISharpPlugin,
                                      ICanLazyLoadGui,
                                      ISupportStatus,
                                      IExtendedNameProvider,
                                      IFrontendController,
                                      IIQStreamController,
                                      IMultiStreamSource,
                                      IConnectableSource,
                                      ITunableSource,
                                      IConfigurationPanelProvider,
                                      ISampleRateChangeSource,
                                      INonBlockingController
    {
        private ControlPanel _gui;
        private ISharpControl _control;
        private readonly HFSdrUdpReceiver _receiver = new HFSdrUdpReceiver();
        private long _frequency = 4900000;

        public string DisplayName => "HF SDR";

        public string Category => "Source";

        public string MenuItemName => DisplayName;

        public bool IsActive => _gui != null && _gui.Visible;

        public UserControl Gui
        {
            get
            {
                LoadGui();
                return _gui;
            }
        }

        UserControl IConfigurationPanelProvider.Gui => Gui;

        public double Samplerate => _receiver.SampleRateHz;

        public RadioStreamType StreamType => RadioStreamType.Complex;

        public SpectrumPolarity SpectrumPolarity => SpectrumPolarity.Positive;

        public bool Connected => _receiver.IsRunning;

        public bool CanTune => true;

        public long Frequency
        {
            get => _frequency;
            set
            {
                _frequency = value;
                _receiver.PendingFrequencyHz = (uint)value;
                if (_receiver.BoardAddress != null)
                {
                    _receiver.TunePendingFrequency();
                }
            }
        }

        public long MinimumTunableFrequency => 0;

        public long MaximumTunableFrequency => 65000000;

        public event System.EventHandler SampleRateChanged;

        public void Initialize(ISharpControl control)
        {
            _control = control;
        }

        public void LoadGui()
        {
            if (_gui == null)
            {
                _gui = new ControlPanel(_control, _receiver);
            }
        }

        public void Close()
        {
            _receiver.Stop();
        }

        public void Open()
        {
            _receiver.Start();
        }

        public void Start(SamplesAvailableDelegate callback)
        {
            _receiver.StartStreaming(this, callback);
            _receiver.Start();
            SampleRateChanged?.Invoke(this, System.EventArgs.Empty);
        }

        public void Stop()
        {
            _receiver.StopStreaming();
        }

        public void Connect()
        {
            _receiver.Start();
        }

        public void Disconnect()
        {
            _receiver.Stop();
        }
    }
}
