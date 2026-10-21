package com.dream.smart_androidbot.service

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.view.Surface
import androidx.core.app.NotificationCompat
import com.dream.smart_androidbot.R
import com.dream.smart_androidbot.config.ConfigManager
import org.java_websocket.client.WebSocketClient
import org.java_websocket.handshake.ServerHandshake
import java.net.URI
import java.util.concurrent.TimeUnit

/** User-authorized screen capture. Control and video use separate sockets.
 * Keep one VirtualDisplay per consent (Android 14+), detach its surface while
 * nobody watches, and request an IDR on every new preview connection.
 */
class LiveVideoService : Service() {
    companion object {
        const val STOP = "com.dream.smart_androidbot.STOP_VIDEO"
        @Volatile private var instance: LiveVideoService? = null
        fun available() = instance?.projection != null
        fun startStream(streamId: String) = instance?.connectStream(streamId)
            ?: error("请在手机 Smart Agent 中点击「开启远程视频」并授权录屏")
        fun stopCurrentStream() { instance?.disconnectStream(null) }
        fun stopStream(streamId: String) { instance?.disconnectStream(streamId) }
    }

    private val guard = Any()
    private val worker = HandlerThread("SAB-video")
    private lateinit var handler: Handler
    @Volatile private var projection: MediaProjection? = null
    private var codec: MediaCodec? = null
    private var surface: Surface? = null
    private var display: VirtualDisplay? = null
    private var socket: WebSocketClient? = null
    private var streamId: String? = null
    private var configBytes = ByteArray(0)
    private var waitingKey = true
    private var queuedSince = 0L

    override fun onCreate() {
        super.onCreate()
        instance = this
        worker.start()
        handler = Handler(worker.looper)
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("sab_video", "远程屏幕视频", NotificationManager.IMPORTANCE_LOW))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP || intent == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        val notification = NotificationCompat.Builder(this, "sab_video")
            .setSmallIcon(R.mipmap.ic_launcher).setContentTitle("Smart Agent · 远程视频")
            .setContentText("网页可查看屏幕；在 App 或系统录屏通知中停止共享")
            .setOngoing(true).build()
        startForeground(1003, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        if (projection != null) return START_NOT_STICKY
        try {
            @Suppress("DEPRECATION")
            val consent = intent.getParcelableExtra<Intent>("consent") ?: error("Missing capture consent")
            val manager = getSystemService(MediaProjectionManager::class.java)
            projection = manager.getMediaProjection(Activity.RESULT_OK, consent)
            projection!!.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() { stopSelf() }
            }, handler)
            synchronized(guard) {
                val metrics = resources.displayMetrics
                val scale = minOf(1f, 1280f / maxOf(metrics.widthPixels, metrics.heightPixels))
                val width = ((metrics.widthPixels * scale).toInt() / 2 * 2).coerceAtLeast(2)
                val height = ((metrics.heightPixels * scale).toInt() / 2 * 2).coerceAtLeast(2)
                val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
                    setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                    setInteger(MediaFormat.KEY_BIT_RATE, 2_000_000)
                    setInteger(MediaFormat.KEY_FRAME_RATE, 32)
                    setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 1_000_000L / 32)
                    setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
                }
                val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
                codec = encoder
                encoder.setCallback(object : MediaCodec.Callback() {
                    override fun onInputBufferAvailable(c: MediaCodec, index: Int) {}
                    override fun onError(c: MediaCodec, error: MediaCodec.CodecException) {
                        android.util.Log.e("SAB-video", "Encoder failed", error)
                        stopSelf()
                    }
                    override fun onOutputFormatChanged(c: MediaCodec, format: MediaFormat) {
                        synchronized(guard) {
                            configBytes = listOf("csd-0", "csd-1").flatMap { key ->
                                val buffer = format.getByteBuffer(key)?.duplicate()
                                if (buffer == null) emptyList() else ByteArray(buffer.remaining()).also { buffer.get(it) }.toList()
                            }.toByteArray()
                        }
                    }
                    override fun onOutputBufferAvailable(c: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
                        synchronized(guard) {
                            if (codec !== c) return
                            try {
                                val buffer = c.getOutputBuffer(index) ?: return
                                buffer.position(info.offset); buffer.limit(info.offset + info.size)
                                val data = ByteArray(info.size).also { buffer.get(it) }
                                if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                                    configBytes = data
                                    return
                                }
                                val client = socket ?: return
                                if (!client.isOpen || data.isEmpty()) return
                                val key = info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
                                if (waitingKey && !key) return
                                val now = android.os.SystemClock.elapsedRealtime()
                                if (client.hasBufferedData()) {
                                    if (queuedSince == 0L) queuedSince = now
                                    if (now - queuedSince > 2000) {
                                        client.close(1000, "Video network too slow")
                                        return
                                    }
                                } else queuedSince = 0L
                                if (waitingKey) {
                                    if (configBytes.isEmpty()) return
                                    client.send(configBytes)
                                    waitingKey = false
                                }
                                client.send(data)
                            } catch (e: Exception) {
                                socket?.close()
                            } finally {
                                runCatching { c.releaseOutputBuffer(index, false) }
                            }
                        }
                    }
                }, handler)
                encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
                surface = encoder.createInputSurface()
                encoder.start()
                display = projection!!.createVirtualDisplay("SAB remote screen", width, height,
                    metrics.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    null, null, handler)
            }
            ReverseConnectionService.publishVideoCapability()
        } catch (e: Exception) {
            android.util.Log.e("SAB-video", "Screen capture setup failed", e)
            stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun connectStream(id: String): String {
        require(id.matches(Regex("[a-f0-9]{32}"))) { "Invalid stream ID" }
        synchronized(guard) {
            check(projection != null && codec != null && display != null) { "录屏授权已结束，请在手机重新开启远程视频" }
            disconnectStream(null)
            val config = ConfigManager.getInstance(this)
            val join = URI(config.serverUrl)
            val path = join.path.substringBeforeLast("/v1/providers/") + "/v1/providers/video"
            val uri = URI(join.scheme, null, join.host, join.port, path, null, null)
            val client = object : WebSocketClient(uri, org.java_websocket.drafts.Draft_6455(),
                mapOf("Authorization" to "Bearer ${config.token}", "X-Stream-ID" to id), 10_000) {
                override fun onOpen(handshake: ServerHandshake?) {}
                override fun onMessage(message: String?) {}
                override fun onError(error: Exception?) {}
                override fun onClose(code: Int, reason: String?, remote: Boolean) {
                    synchronized(guard) { if (this@LiveVideoService.socket === this) disconnectStream(id) }
                }
            }
            streamId = id
            socket = client
        }
        val client = synchronized(guard) { socket!! }
        // Never block the codec callback lock while waiting for network IO.
        if (!client.connectBlocking(10, TimeUnit.SECONDS)) {
            disconnectStream(id)
            error("远程视频通道连接失败")
        }
        synchronized(guard) {
            if (socket !== client) error("视频请求已取消")
            waitingKey = true
            queuedSince = 0
            display?.surface = surface
            codec?.setParameters(Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) })
        }
        return "Video started"
    }

    private fun disconnectStream(id: String?) {
        synchronized(guard) {
            if (id != null && streamId != id) return
            val previous = socket
            socket = null
            streamId = null
            runCatching { display?.surface = null }
            previous?.close()
        }
    }

    override fun onDestroy() {
        synchronized(guard) {
            disconnectStream(null)
            display?.release(); display = null
            val previous = codec; codec = null
            runCatching { previous?.stop() }; runCatching { previous?.release() }
            surface?.release(); surface = null
            val capture = projection; projection = null
            capture?.stop()
        }
        if (instance === this) instance = null
        ReverseConnectionService.publishVideoCapability()
        worker.quitSafely()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
