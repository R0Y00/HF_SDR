namespace SDRSharp.HFSDR
{
    partial class ControlPanel
    {
        private System.ComponentModel.IContainer components = null;
        private System.Windows.Forms.Button startButton;
        private System.Windows.Forms.Button stopButton;
        private System.Windows.Forms.NumericUpDown dataPortBox;
        private System.Windows.Forms.NumericUpDown ctrlPortBox;
        private System.Windows.Forms.TextBox frequencyBox;
        private System.Windows.Forms.Button tuneButton;
        private System.Windows.Forms.Label statusLabel;
        private System.Windows.Forms.Label boardLabel;
        private System.Windows.Forms.Label packetsLabel;
        private System.Windows.Forms.Label lostLabel;
        private System.Windows.Forms.Label badLabel;
        private System.Windows.Forms.Label rateLabel;
        private System.Windows.Forms.Label sampleRateLabel;
        private System.Windows.Forms.Label centerLabel;

        private void InitializeComponent()
        {
            this.startButton = new System.Windows.Forms.Button();
            this.stopButton = new System.Windows.Forms.Button();
            this.dataPortBox = new System.Windows.Forms.NumericUpDown();
            this.ctrlPortBox = new System.Windows.Forms.NumericUpDown();
            this.frequencyBox = new System.Windows.Forms.TextBox();
            this.tuneButton = new System.Windows.Forms.Button();
            this.statusLabel = new System.Windows.Forms.Label();
            this.boardLabel = new System.Windows.Forms.Label();
            this.packetsLabel = new System.Windows.Forms.Label();
            this.lostLabel = new System.Windows.Forms.Label();
            this.badLabel = new System.Windows.Forms.Label();
            this.rateLabel = new System.Windows.Forms.Label();
            this.sampleRateLabel = new System.Windows.Forms.Label();
            this.centerLabel = new System.Windows.Forms.Label();
            var titleLabel = new System.Windows.Forms.Label();
            var dataPortLabel = new System.Windows.Forms.Label();
            var ctrlPortLabel = new System.Windows.Forms.Label();
            var freqLabel = new System.Windows.Forms.Label();
            var boardTextLabel = new System.Windows.Forms.Label();
            var packetsTextLabel = new System.Windows.Forms.Label();
            var lostTextLabel = new System.Windows.Forms.Label();
            var badTextLabel = new System.Windows.Forms.Label();
            var rateTextLabel = new System.Windows.Forms.Label();
            var sampleRateTextLabel = new System.Windows.Forms.Label();
            var centerTextLabel = new System.Windows.Forms.Label();
            ((System.ComponentModel.ISupportInitialize)(this.dataPortBox)).BeginInit();
            ((System.ComponentModel.ISupportInitialize)(this.ctrlPortBox)).BeginInit();
            this.SuspendLayout();
            // 
            // titleLabel
            // 
            titleLabel.AutoSize = true;
            titleLabel.Font = new System.Drawing.Font("Segoe UI", 10F, System.Drawing.FontStyle.Bold);
            titleLabel.Location = new System.Drawing.Point(8, 8);
            titleLabel.Name = "titleLabel";
            titleLabel.Size = new System.Drawing.Size(116, 19);
            titleLabel.TabIndex = 0;
            titleLabel.Text = "HF SDR Monitor";
            // 
            // dataPortLabel
            // 
            dataPortLabel.AutoSize = true;
            dataPortLabel.Location = new System.Drawing.Point(8, 42);
            dataPortLabel.Name = "dataPortLabel";
            dataPortLabel.Size = new System.Drawing.Size(55, 15);
            dataPortLabel.TabIndex = 1;
            dataPortLabel.Text = "UDP data";
            // 
            // dataPortBox
            // 
            this.dataPortBox.Location = new System.Drawing.Point(90, 39);
            this.dataPortBox.Maximum = new decimal(new int[] { 65535, 0, 0, 0 });
            this.dataPortBox.Minimum = new decimal(new int[] { 1, 0, 0, 0 });
            this.dataPortBox.Name = "dataPortBox";
            this.dataPortBox.Size = new System.Drawing.Size(82, 23);
            this.dataPortBox.TabIndex = 2;
            this.dataPortBox.Value = new decimal(new int[] { 5001, 0, 0, 0 });
            // 
            // ctrlPortLabel
            // 
            ctrlPortLabel.AutoSize = true;
            ctrlPortLabel.Location = new System.Drawing.Point(8, 72);
            ctrlPortLabel.Name = "ctrlPortLabel";
            ctrlPortLabel.Size = new System.Drawing.Size(71, 15);
            ctrlPortLabel.TabIndex = 3;
            ctrlPortLabel.Text = "UDP control";
            // 
            // ctrlPortBox
            // 
            this.ctrlPortBox.Location = new System.Drawing.Point(90, 69);
            this.ctrlPortBox.Maximum = new decimal(new int[] { 65535, 0, 0, 0 });
            this.ctrlPortBox.Minimum = new decimal(new int[] { 1, 0, 0, 0 });
            this.ctrlPortBox.Name = "ctrlPortBox";
            this.ctrlPortBox.Size = new System.Drawing.Size(82, 23);
            this.ctrlPortBox.TabIndex = 4;
            this.ctrlPortBox.Value = new decimal(new int[] { 5002, 0, 0, 0 });
            // 
            // startButton
            // 
            this.startButton.Location = new System.Drawing.Point(8, 102);
            this.startButton.Name = "startButton";
            this.startButton.Size = new System.Drawing.Size(78, 26);
            this.startButton.TabIndex = 5;
            this.startButton.Text = "Start";
            this.startButton.UseVisualStyleBackColor = true;
            this.startButton.Click += new System.EventHandler(this.startButton_Click);
            // 
            // stopButton
            // 
            this.stopButton.Location = new System.Drawing.Point(94, 102);
            this.stopButton.Name = "stopButton";
            this.stopButton.Size = new System.Drawing.Size(78, 26);
            this.stopButton.TabIndex = 6;
            this.stopButton.Text = "Stop";
            this.stopButton.UseVisualStyleBackColor = true;
            this.stopButton.Click += new System.EventHandler(this.stopButton_Click);
            // 
            // freqLabel
            // 
            freqLabel.AutoSize = true;
            freqLabel.Location = new System.Drawing.Point(8, 146);
            freqLabel.Name = "freqLabel";
            freqLabel.Size = new System.Drawing.Size(80, 15);
            freqLabel.TabIndex = 7;
            freqLabel.Text = "Tune Hz";
            // 
            // frequencyBox
            // 
            this.frequencyBox.Location = new System.Drawing.Point(90, 143);
            this.frequencyBox.Name = "frequencyBox";
            this.frequencyBox.Size = new System.Drawing.Size(104, 23);
            this.frequencyBox.TabIndex = 8;
            // 
            // tuneButton
            // 
            this.tuneButton.Location = new System.Drawing.Point(200, 142);
            this.tuneButton.Name = "tuneButton";
            this.tuneButton.Size = new System.Drawing.Size(56, 25);
            this.tuneButton.TabIndex = 9;
            this.tuneButton.Text = "Tune";
            this.tuneButton.UseVisualStyleBackColor = true;
            this.tuneButton.Click += new System.EventHandler(this.tuneButton_Click);
            // 
            // statusLabel
            // 
            this.statusLabel.AutoSize = true;
            this.statusLabel.Font = new System.Drawing.Font("Segoe UI", 9F, System.Drawing.FontStyle.Bold);
            this.statusLabel.Location = new System.Drawing.Point(8, 182);
            this.statusLabel.Name = "statusLabel";
            this.statusLabel.Size = new System.Drawing.Size(52, 15);
            this.statusLabel.TabIndex = 10;
            this.statusLabel.Text = "Stopped";
            // 
            // boardTextLabel
            // 
            boardTextLabel.AutoSize = true;
            boardTextLabel.Location = new System.Drawing.Point(8, 210);
            boardTextLabel.Name = "boardTextLabel";
            boardTextLabel.Size = new System.Drawing.Size(38, 15);
            boardTextLabel.TabIndex = 11;
            boardTextLabel.Text = "Board";
            // 
            // boardLabel
            // 
            this.boardLabel.AutoSize = true;
            this.boardLabel.Location = new System.Drawing.Point(112, 210);
            this.boardLabel.Name = "boardLabel";
            this.boardLabel.Size = new System.Drawing.Size(12, 15);
            this.boardLabel.TabIndex = 12;
            this.boardLabel.Text = "-";
            // 
            // packetsTextLabel
            // 
            packetsTextLabel.AutoSize = true;
            packetsTextLabel.Location = new System.Drawing.Point(8, 235);
            packetsTextLabel.Name = "packetsTextLabel";
            packetsTextLabel.Size = new System.Drawing.Size(47, 15);
            packetsTextLabel.TabIndex = 13;
            packetsTextLabel.Text = "Packets";
            // 
            // packetsLabel
            // 
            this.packetsLabel.AutoSize = true;
            this.packetsLabel.Location = new System.Drawing.Point(112, 235);
            this.packetsLabel.Name = "packetsLabel";
            this.packetsLabel.Size = new System.Drawing.Size(13, 15);
            this.packetsLabel.TabIndex = 14;
            this.packetsLabel.Text = "0";
            // 
            // lostTextLabel
            // 
            lostTextLabel.AutoSize = true;
            lostTextLabel.Location = new System.Drawing.Point(8, 260);
            lostTextLabel.Name = "lostTextLabel";
            lostTextLabel.Size = new System.Drawing.Size(28, 15);
            lostTextLabel.TabIndex = 15;
            lostTextLabel.Text = "Lost";
            // 
            // lostLabel
            // 
            this.lostLabel.AutoSize = true;
            this.lostLabel.Location = new System.Drawing.Point(112, 260);
            this.lostLabel.Name = "lostLabel";
            this.lostLabel.Size = new System.Drawing.Size(13, 15);
            this.lostLabel.TabIndex = 16;
            this.lostLabel.Text = "0";
            // 
            // badTextLabel
            // 
            badTextLabel.AutoSize = true;
            badTextLabel.Location = new System.Drawing.Point(8, 285);
            badTextLabel.Name = "badTextLabel";
            badTextLabel.Size = new System.Drawing.Size(26, 15);
            badTextLabel.TabIndex = 17;
            badTextLabel.Text = "Bad";
            // 
            // badLabel
            // 
            this.badLabel.AutoSize = true;
            this.badLabel.Location = new System.Drawing.Point(112, 285);
            this.badLabel.Name = "badLabel";
            this.badLabel.Size = new System.Drawing.Size(13, 15);
            this.badLabel.TabIndex = 18;
            this.badLabel.Text = "0";
            // 
            // rateTextLabel
            // 
            rateTextLabel.AutoSize = true;
            rateTextLabel.Location = new System.Drawing.Point(8, 310);
            rateTextLabel.Name = "rateTextLabel";
            rateTextLabel.Size = new System.Drawing.Size(61, 15);
            rateTextLabel.TabIndex = 19;
            rateTextLabel.Text = "Measured";
            // 
            // rateLabel
            // 
            this.rateLabel.AutoSize = true;
            this.rateLabel.Location = new System.Drawing.Point(112, 310);
            this.rateLabel.Name = "rateLabel";
            this.rateLabel.Size = new System.Drawing.Size(40, 15);
            this.rateLabel.TabIndex = 20;
            this.rateLabel.Text = "0 IQ/s";
            // 
            // sampleRateTextLabel
            // 
            sampleRateTextLabel.AutoSize = true;
            sampleRateTextLabel.Location = new System.Drawing.Point(8, 335);
            sampleRateTextLabel.Name = "sampleRateTextLabel";
            sampleRateTextLabel.Size = new System.Drawing.Size(68, 15);
            sampleRateTextLabel.TabIndex = 21;
            sampleRateTextLabel.Text = "Header rate";
            // 
            // sampleRateLabel
            // 
            this.sampleRateLabel.AutoSize = true;
            this.sampleRateLabel.Location = new System.Drawing.Point(112, 335);
            this.sampleRateLabel.Name = "sampleRateLabel";
            this.sampleRateLabel.Size = new System.Drawing.Size(35, 15);
            this.sampleRateLabel.TabIndex = 22;
            this.sampleRateLabel.Text = "0 S/s";
            // 
            // centerTextLabel
            // 
            centerTextLabel.AutoSize = true;
            centerTextLabel.Location = new System.Drawing.Point(8, 360);
            centerTextLabel.Name = "centerTextLabel";
            centerTextLabel.Size = new System.Drawing.Size(42, 15);
            centerTextLabel.TabIndex = 23;
            centerTextLabel.Text = "Center";
            // 
            // centerLabel
            // 
            this.centerLabel.AutoSize = true;
            this.centerLabel.Location = new System.Drawing.Point(112, 360);
            this.centerLabel.Name = "centerLabel";
            this.centerLabel.Size = new System.Drawing.Size(28, 15);
            this.centerLabel.TabIndex = 24;
            this.centerLabel.Text = "0 Hz";
            // 
            // ControlPanel
            // 
            this.AutoScaleDimensions = new System.Drawing.SizeF(7F, 15F);
            this.AutoScaleMode = System.Windows.Forms.AutoScaleMode.Font;
            this.Controls.Add(this.centerLabel);
            this.Controls.Add(centerTextLabel);
            this.Controls.Add(this.sampleRateLabel);
            this.Controls.Add(sampleRateTextLabel);
            this.Controls.Add(this.rateLabel);
            this.Controls.Add(rateTextLabel);
            this.Controls.Add(this.badLabel);
            this.Controls.Add(badTextLabel);
            this.Controls.Add(this.lostLabel);
            this.Controls.Add(lostTextLabel);
            this.Controls.Add(this.packetsLabel);
            this.Controls.Add(packetsTextLabel);
            this.Controls.Add(this.boardLabel);
            this.Controls.Add(boardTextLabel);
            this.Controls.Add(this.statusLabel);
            this.Controls.Add(this.tuneButton);
            this.Controls.Add(this.frequencyBox);
            this.Controls.Add(freqLabel);
            this.Controls.Add(this.stopButton);
            this.Controls.Add(this.startButton);
            this.Controls.Add(this.ctrlPortBox);
            this.Controls.Add(ctrlPortLabel);
            this.Controls.Add(this.dataPortBox);
            this.Controls.Add(dataPortLabel);
            this.Controls.Add(titleLabel);
            this.Name = "ControlPanel";
            this.Size = new System.Drawing.Size(270, 392);
            ((System.ComponentModel.ISupportInitialize)(this.dataPortBox)).EndInit();
            ((System.ComponentModel.ISupportInitialize)(this.ctrlPortBox)).EndInit();
            this.ResumeLayout(false);
            this.PerformLayout();
        }
    }
}
