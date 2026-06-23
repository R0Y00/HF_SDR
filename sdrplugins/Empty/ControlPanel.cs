using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Drawing;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using SDRSharp.Common;
using SDRSharp.Radio;

namespace SDRSharp.Empty
{
    public partial class ControlPanel : UserControl
    {
        private ISharpControl _control;

        public ControlPanel(ISharpControl control)
        {
            _control = control;
            InitializeComponent();
        }

        private void someButton_Click(object sender, EventArgs e)
        {
            MessageBox.Show("Hey!", "Messagge", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
        }
    }
}
