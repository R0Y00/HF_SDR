using System;
using System.Globalization;
using System.Windows.Forms;
using SDRSharp.Common;

namespace SDRSharp.HFSDR
{
    public partial class ControlPanel : UserControl
    {
        private readonly ISharpControl _control;
        private readonly HFSdrUdpReceiver _receiver;
        private readonly Timer _timer;

        public ControlPanel(ISharpControl control, HFSdrUdpReceiver receiver)
        {
            _control = control;
            _receiver = receiver;
            InitializeComponent();

            dataPortBox.Value = HFSdrUdpReceiver.DefaultDataPort;
            ctrlPortBox.Value = HFSdrUdpReceiver.DefaultControlPort;
            frequencyBox.Text = "4900000";

            _timer = new Timer { Interval = 500 };
            _timer.Tick += timer_Tick;
            _timer.Start();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _timer?.Stop();
                _timer?.Dispose();
                components?.Dispose();
            }
            base.Dispose(disposing);
        }

        private void startButton_Click(object sender, EventArgs e)
        {
            try
            {
                _receiver.DataPort = (int)dataPortBox.Value;
                _receiver.ControlPort = (int)ctrlPortBox.Value;
                _receiver.Start();
                statusLabel.Text = "Listening";
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "HF SDR", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void stopButton_Click(object sender, EventArgs e)
        {
            _receiver.Stop();
            statusLabel.Text = "Stopped";
        }

        private void tuneButton_Click(object sender, EventArgs e)
        {
            if (!uint.TryParse(frequencyBox.Text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var frequencyHz))
            {
                MessageBox.Show("Enter frequency in Hz.", "HF SDR", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            try
            {
                _receiver.Tune(frequencyHz);
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "HF SDR", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void timer_Tick(object sender, EventArgs e)
        {
            var snapshot = _receiver.Snapshot();
            statusLabel.Text = snapshot.Running ? "Listening" : "Stopped";
            boardLabel.Text = snapshot.BoardAddress;
            packetsLabel.Text = snapshot.Packets.ToString(CultureInfo.InvariantCulture);
            lostLabel.Text = snapshot.LostPackets.ToString(CultureInfo.InvariantCulture);
            badLabel.Text = snapshot.BadPackets.ToString(CultureInfo.InvariantCulture);
            rateLabel.Text = snapshot.MeasuredIqRate.ToString("F0", CultureInfo.InvariantCulture) + " IQ/s";
            sampleRateLabel.Text = snapshot.SampleRateHz.ToString(CultureInfo.InvariantCulture) + " S/s";
            centerLabel.Text = snapshot.CenterFrequencyHz.ToString(CultureInfo.InvariantCulture) + " Hz";
        }
    }
}
