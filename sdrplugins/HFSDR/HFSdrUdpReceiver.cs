using System;
using System.Buffers.Binary;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using SDRSharp.Radio;

namespace SDRSharp.HFSDR
{
    public sealed class HFSdrUdpReceiver
    {
        public const int DefaultDataPort = 5001;
        public const int DefaultControlPort = 5002;
        public const int SamplesPerPacket = 256;
        public const int PayloadBytes = 1024;
        public const int HeaderBytes = 24;
        private const uint Magic = 0x52534648;
        private const ushort SampleFormatIqS16 = 2;

        private readonly object _lock = new object();
        private readonly short[] _latestWords = new short[SamplesPerPacket * 2];
        private UdpClient _udp;
        private Thread _thread;
        private volatile bool _running;
        private uint _lastSequence;
        private bool _hasSequence;
        private long _packetCount;
        private long _lostPackets;
        private long _badPackets;
        private long _sampleCount;
        private readonly Stopwatch _stopwatch = new Stopwatch();
        private SamplesAvailableDelegate _streamCallback;
        private IFrontendController _streamOwner;
        private uint _lastTuneSentHz;

        public int DataPort { get; set; } = DefaultDataPort;

        public int ControlPort { get; set; } = DefaultControlPort;

        public uint PendingFrequencyHz { get; set; } = 4900000;

        public IPAddress BoardAddress { get; private set; }

        public uint CenterFrequencyHz { get; private set; } = 4900000;

        public uint SampleRateHz { get; private set; } = 253906;

        public bool IsRunning => _running;

        public void Start()
        {
            if (_running)
            {
                return;
            }

            _running = true;
            _hasSequence = false;
            _packetCount = 0;
            _lostPackets = 0;
            _badPackets = 0;
            _sampleCount = 0;
            _stopwatch.Restart();

            _udp = new UdpClient(DataPort);
            _udp.Client.ReceiveBufferSize = 8 * 1024 * 1024;
            _thread = new Thread(ReceiveLoop)
            {
                IsBackground = true,
                Name = "HF SDR UDP Receiver"
            };
            _thread.Start();
        }

        public void Stop()
        {
            _running = false;
            try
            {
                _udp?.Close();
            }
            catch (SocketException)
            {
            }
            _udp = null;
        }

        public ReceiverSnapshot Snapshot()
        {
            lock (_lock)
            {
                var elapsed = Math.Max(_stopwatch.Elapsed.TotalSeconds, 0.001);
                return new ReceiverSnapshot
                {
                    Running = _running,
                    BoardAddress = BoardAddress?.ToString() ?? "-",
                    Packets = _packetCount,
                    LostPackets = _lostPackets,
                    BadPackets = _badPackets,
                    SampleRateHz = SampleRateHz,
                    CenterFrequencyHz = CenterFrequencyHz,
                    MeasuredIqRate = _sampleCount / elapsed
                };
            }
        }

        public short[] CopyLatestWords()
        {
            lock (_lock)
            {
                var copy = new short[_latestWords.Length];
                Array.Copy(_latestWords, copy, _latestWords.Length);
                return copy;
            }
        }

        public void StartStreaming(IFrontendController owner, SamplesAvailableDelegate callback)
        {
            lock (_lock)
            {
                _streamOwner = owner;
                _streamCallback = callback;
            }
        }

        public void StopStreaming()
        {
            lock (_lock)
            {
                _streamCallback = null;
                _streamOwner = null;
            }
        }

        public void Tune(uint frequencyHz)
        {
            var address = BoardAddress;
            if (address == null)
            {
                throw new InvalidOperationException("No board address has been seen yet.");
            }

            using (var client = new UdpClient())
            {
                var bytes = System.Text.Encoding.ASCII.GetBytes("FREQ " + frequencyHz + "\n");
                client.Send(bytes, bytes.Length, new IPEndPoint(address, ControlPort));
            }
        }

        public void TunePendingFrequency()
        {
            var frequencyHz = PendingFrequencyHz;
            if (frequencyHz == 0 || BoardAddress == null || frequencyHz == _lastTuneSentHz)
            {
                return;
            }

            Tune(frequencyHz);
            _lastTuneSentHz = frequencyHz;
        }

        private void ReceiveLoop()
        {
            var remote = new IPEndPoint(IPAddress.Any, 0);
            while (_running)
            {
                try
                {
                    var data = _udp.Receive(ref remote);
                    ParsePacket(data, remote.Address);
                }
                catch (ObjectDisposedException)
                {
                    break;
                }
                catch (SocketException)
                {
                    if (_running)
                    {
                        CountBadPacket();
                    }
                }
            }
        }

        private void CountBadPacket()
        {
            lock (_lock)
            {
                _badPackets++;
            }
        }

        private void ParsePacket(byte[] data, IPAddress boardAddress)
        {
            if (data.Length < HeaderBytes)
            {
                CountBadPacket();
                return;
            }

            var magic = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(0, 4));
            var sequence = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4, 4));
            var headerBytes = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(8, 2));
            var sampleCount = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(10, 2));
            var sampleFormat = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(12, 2));
            var payloadBytes = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(14, 2));

            if (magic != Magic ||
                sampleFormat != SampleFormatIqS16 ||
                sampleCount != SamplesPerPacket ||
                payloadBytes != PayloadBytes ||
                headerBytes > data.Length ||
                headerBytes + payloadBytes > data.Length)
            {
                CountBadPacket();
                return;
            }

            var centerHz = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(16, 4));
            var sampleRateHz = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(20, 4));

            lock (_lock)
            {
                BoardAddress = boardAddress;
                CenterFrequencyHz = centerHz;
                SampleRateHz = sampleRateHz;
                if (_hasSequence)
                {
                    var expected = _lastSequence + 1;
                    if (sequence != expected)
                    {
                        var gap = unchecked(sequence - expected);
                        if (gap < 1000000)
                        {
                            _lostPackets += gap;
                        }
                    }
                }
                _hasSequence = true;
                _lastSequence = sequence;
                _packetCount++;
                _sampleCount += sampleCount;

                var payload = data.AsSpan(headerBytes, payloadBytes);
                for (var i = 0; i < _latestWords.Length; i++)
                {
                    _latestWords[i] = BinaryPrimitives.ReadInt16LittleEndian(payload.Slice(i * 2, 2));
                }
            }

            try
            {
                TunePendingFrequency();
            }
            catch (SocketException)
            {
            }

            EmitSamples(data, headerBytes);
        }

        private unsafe void EmitSamples(byte[] data, int headerBytes)
        {
            SamplesAvailableDelegate callback;
            IFrontendController owner;
            lock (_lock)
            {
                callback = _streamCallback;
                owner = _streamOwner;
            }

            if (callback == null || owner == null)
            {
                return;
            }

            var samples = new Complex[SamplesPerPacket];
            var payload = data.AsSpan(headerBytes, PayloadBytes);
            for (var i = 0; i < SamplesPerPacket; i++)
            {
                var i16 = BinaryPrimitives.ReadInt16LittleEndian(payload.Slice(i * 4, 2));
                var q16 = BinaryPrimitives.ReadInt16LittleEndian(payload.Slice(i * 4 + 2, 2));
                samples[i].Real = i16 / 32768.0f;
                samples[i].Imag = q16 / 32768.0f;
            }

            fixed (Complex* ptr = samples)
            {
                callback(owner, ptr, samples.Length);
            }
        }
    }

    public struct ReceiverSnapshot
    {
        public bool Running;
        public string BoardAddress;
        public long Packets;
        public long LostPackets;
        public long BadPackets;
        public uint CenterFrequencyHz;
        public uint SampleRateHz;
        public double MeasuredIqRate;
    }
}
