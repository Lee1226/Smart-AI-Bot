export type WebGLRendererStats = {
  width: number
  height: number
}

const VERTEX_SHADER = `
attribute vec2 a_position;
attribute vec2 a_texCoord;
varying vec2 v_texCoord;
void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
  v_texCoord = a_texCoord;
}
`

const FRAGMENT_SHADER = `
precision mediump float;
varying vec2 v_texCoord;
uniform sampler2D u_texture;
void main() {
  gl_FragColor = texture2D(u_texture, v_texCoord);
}
`

export class WebGLRenderer {
  private gl: WebGLRenderingContext
  private texture: WebGLTexture
  private program: WebGLProgram
  private positionBuffer: WebGLBuffer
  private texCoordBuffer: WebGLBuffer
  private positionLocation: number
  private texCoordLocation: number
  private textureLocation: WebGLUniformLocation | null
  private disposed = false

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext('webgl', {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    })
    if (!gl) throw new Error('当前浏览器无法创建 WebGL 上下文')
    this.gl = gl

    const vertex = this.compile(gl.VERTEX_SHADER, VERTEX_SHADER)
    const fragment = this.compile(gl.FRAGMENT_SHADER, FRAGMENT_SHADER)
    const program = gl.createProgram()
    if (!program) throw new Error('无法创建 WebGL Program')
    gl.attachShader(program, vertex)
    gl.attachShader(program, fragment)
    gl.linkProgram(program)
    gl.deleteShader(vertex)
    gl.deleteShader(fragment)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const reason = gl.getProgramInfoLog(program) || 'unknown'
      gl.deleteProgram(program)
      throw new Error(`WebGL Program 链接失败：${reason}`)
    }
    this.program = program

    const texture = gl.createTexture()
    const positionBuffer = gl.createBuffer()
    const texCoordBuffer = gl.createBuffer()
    if (!texture || !positionBuffer || !texCoordBuffer) {
      throw new Error('无法创建 WebGL 渲染资源')
    }
    this.texture = texture
    this.positionBuffer = positionBuffer
    this.texCoordBuffer = texCoordBuffer
    this.positionLocation = gl.getAttribLocation(program, 'a_position')
    this.texCoordLocation = gl.getAttribLocation(program, 'a_texCoord')
    this.textureLocation = gl.getUniformLocation(program, 'u_texture')

    gl.useProgram(program)
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -1, -1, 1, -1, -1, 1, 1, 1,
    ]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(this.positionLocation)
    gl.vertexAttribPointer(this.positionLocation, 2, gl.FLOAT, false, 0, 0)

    gl.bindBuffer(gl.ARRAY_BUFFER, texCoordBuffer)
    // Flip Y: WebGL texture coordinates and video frame coordinates differ.
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      0, 1, 1, 1, 0, 0, 1, 0,
    ]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(this.texCoordLocation)
    gl.vertexAttribPointer(this.texCoordLocation, 2, gl.FLOAT, false, 0, 0)

    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.uniform1i(this.textureLocation, 0)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0)
  }

  private compile(type: number, source: string): WebGLShader {
    const shader = this.gl.createShader(type)
    if (!shader) throw new Error('无法创建 WebGL Shader')
    this.gl.shaderSource(shader, source)
    this.gl.compileShader(shader)
    if (!this.gl.getShaderParameter(shader, this.gl.COMPILE_STATUS)) {
      const reason = this.gl.getShaderInfoLog(shader) || 'unknown'
      this.gl.deleteShader(shader)
      throw new Error(`WebGL Shader 编译失败：${reason}`)
    }
    return shader
  }

  render(frame: VideoFrame): WebGLRendererStats {
    if (this.disposed) throw new Error('WebGL Renderer 已释放')
    const gl = this.gl
    const width = frame.displayWidth
    const height = frame.displayHeight
    if (this.canvas.width !== width) this.canvas.width = width
    if (this.canvas.height !== height) this.canvas.height = height
    gl.viewport(0, 0, width, height)
    gl.useProgram(this.program)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.texture)
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      frame as unknown as TexImageSource,
    )
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
    return { width, height }
  }

  dispose() {
    if (this.disposed) return
    this.disposed = true
    const gl = this.gl
    gl.deleteTexture(this.texture)
    gl.deleteBuffer(this.positionBuffer)
    gl.deleteBuffer(this.texCoordBuffer)
    gl.deleteProgram(this.program)
    const lose = gl.getExtension('WEBGL_lose_context')
    lose?.loseContext()
  }
}
