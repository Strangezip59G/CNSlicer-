// CNSlicer — Subtractive Studio Version 1
let scene, camera, renderer, controls, transformControls;
let bedGrid, stockMesh, partMeshGroup, toolpathLinesGroup, toolMeshGroup;
let silhouetteOutlineGroup, featureEdgesGroup;
let currentMesh = null;
let currentSlicingData = null;
let currentModelType = "sample2";
let latestAnalysis = null;
let activeTool = "select"; // 'select', 'translate', 'rotate', 'scale', 'mirror'
let isSimulating = false;
let simIndex = 0;
let simPoints = [];
let simInterval = null;

// Machine Profile & Bed Layout State
let currentMachine = null;
let machinesList = [];
let travelCageMesh = null;
let bedFeaturesMesh = null;
let bedNumbersMesh = null;

// Universal Status Bar & Pulse Dot Telemetry Helper
function setStatus(text, state = "ready") {
  const elText = document.getElementById("status-text");
  const elDot = document.getElementById("status-pulse-dot");
  if (elText) elText.textContent = text;
  if (elDot) {
    elDot.className = "status-indicator-dot";
    if (state === "busy" || state === "amber") {
      elDot.classList.add("busy-amber");
    } else if (state === "scan" || state === "purple") {
      elDot.classList.add("scan-purple");
    } else if (state === "error" || state === "red") {
      elDot.classList.add("error-red");
    }
  }
}

// Initialize Three.js 3D Viewport
function initViewport() {
  const container = document.getElementById("viewport-3d");
  const width = container.clientWidth;
  const height = container.clientHeight;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x121214);

  // Perspective camera (Z is UP in CNC coordinates)
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
  camera.position.set(160, -220, 180);
  camera.up.set(0, 0, 1);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(window.devicePixelRatio);
  container.appendChild(renderer.domElement);

  // Orbit controls (Orbit / Pan / Zoom)
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 15);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;

  // Clear camera preset active highlight when user manually orbits
  controls.addEventListener("start", () => {
    document.querySelectorAll(".views-control-capsule .btn-view-pill").forEach(btn => {
      btn.classList.remove("active");
    });
  });

  // Transform Gizmo Controls (Creality Slicer 3D handles)
  transformControls = new THREE.TransformControls(camera, renderer.domElement);
  transformControls.size = 0.75;
  transformControls.space = "local";
  scene.add(transformControls);

  // Disable OrbitControls while dragging gizmo
  transformControls.addEventListener("dragging-changed", (event) => {
    controls.enabled = !event.value;
  });

  // Sync 3D Gizmo drags back to input drawer fields and move stock if locked
  transformControls.addEventListener("objectChange", () => {
    if (!currentMesh) return;
    syncMeshToUI();
    if (document.getElementById("stock-lock-to-model").checked) {
      syncStockToModelPosition();
    }
  });

  // Lighting
  const ambLight = new THREE.AmbientLight(0xffffff, 0.65);
  scene.add(ambLight);

  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.85);
  dirLight1.position.set(200, 200, 300);
  scene.add(dirLight1);

  const dirLight2 = new THREE.DirectionalLight(0x90caf9, 0.45);
  dirLight2.position.set(-200, -200, 100);
  scene.add(dirLight2);

  // Groups
  partMeshGroup = new THREE.Group();
  toolpathLinesGroup = new THREE.Group();
  silhouetteOutlineGroup = new THREE.Group();
  featureEdgesGroup = new THREE.Group();

  scene.add(partMeshGroup);
  scene.add(toolpathLinesGroup);
  scene.add(silhouetteOutlineGroup);
  scene.add(featureEdgesGroup);

  // 3D Cutting Tool Mesh
  toolMeshGroup = createToolMesh(3.175);
  scene.add(toolMeshGroup);
  toolMeshGroup.visible = false;

  // Initialize CNC Machine Profiles & Bed Layout (3D Printer Slicer Style)
  loadMachineProfiles();

  // Initial default stock block
  renderCurrentStockMesh();

  // Load default sample 3D pyramid
  loadSamplePyramid();

  // Resize handler
  window.addEventListener("resize", onWindowResize);

  // Drag-and-drop STL handler on canvas
  setupDragAndDrop(container);

  // Setup Creality tool listeners, machine listeners, and analysis listeners
  setupCrealityControls();
  setupMachineControls();
  setupStockAndAnalysisControls();

  animate();
}

function onWindowResize() {
  const container = document.getElementById("viewport-3d");
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}

// Generate crisp, branded 3D canvas texture for machine model nameplate badge
function createMachineNameplateTexture(name, dims) {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 96;
  const ctx = canvas.getContext("2d");

  // Brushed dark metal background
  ctx.fillStyle = "#16171b";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // High-tech neon cyan border
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 4;
  ctx.strokeRect(4, 4, canvas.width - 8, canvas.height - 8);

  // Decorative corner brackets
  ctx.fillStyle = "#38bdf8";
  ctx.fillRect(2, 2, 8, 8);
  ctx.fillRect(canvas.width - 10, 2, 8, 8);
  ctx.fillRect(2, canvas.height - 10, 8, 8);
  ctx.fillRect(canvas.width - 10, canvas.height - 10, 8, 8);

  // Model name
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 28px 'Segoe UI', Arial, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(name.toUpperCase(), canvas.width / 2, 38);

  // Dimension specs
  ctx.fillStyle = "#94a3b8";
  ctx.font = "18px Consolas, monospace";
  ctx.fillText(dims, canvas.width / 2, 70);

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  return texture;
}

// Helper: Determine spacing of ruler millimeter markings based on bed dimension
function getRulerStepInfo(dim) {
  let minorStep = 10;
  let midStep = 25;
  let majorStep = 50;

  if (dim <= 150) {
    minorStep = 5;
    midStep = 10;
    majorStep = 25;
  } else if (dim <= 320) {
    minorStep = 10;
    midStep = 25;
    majorStep = 50;
  } else if (dim <= 650) {
    minorStep = 10;
    midStep = 25;
    majorStep = 50;
  } else {
    minorStep = 25;
    midStep = 50;
    majorStep = 100;
  }
  return { minorStep, midStep, majorStep };
}

// Safely draw rounded rectangle in Canvas 2D
function drawCanvasRoundRect(ctx, x, y, w, h, r) {
  if (ctx.roundRect) {
    ctx.roundRect(x, y, w, h, r);
  } else {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }
}

// Generate High-Precision Metric Ruler Texture for X-Axis (Front or Rear Edge)
function createRulerTextureX(w, isRear = false) {
  const canvas = document.createElement("canvas");
  canvas.width = 2048;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");

  const { minorStep, midStep, majorStep } = getRulerStepInfo(w);
  const xMin = -w / 2;

  // Dark metallic ruler background
  ctx.fillStyle = "#111319";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Subtle brushed metallic band
  ctx.fillStyle = "#181c25";
  ctx.fillRect(0, 3, canvas.width, canvas.height - 6);

  // Grid border baseline (facing bed interior)
  const tickBaseY = isRear ? canvas.height - 2 : 2;
  ctx.fillStyle = "#38bdf8";
  ctx.fillRect(0, isRear ? canvas.height - 4 : 2, canvas.width, 3);

  // Outer edge trim
  ctx.fillStyle = "#334155";
  ctx.fillRect(0, isRear ? 1 : canvas.height - 3, canvas.width, 2);

  const startX = Math.ceil(xMin / minorStep) * minorStep;
  const endX = Math.floor((w / 2) / minorStep) * minorStep;

  for (let x = startX; x <= endX; x += minorStep) {
    const u = ((x - xMin) / w) * canvas.width;
    if (u < 4 || u > canvas.width - 4) continue;

    const isMajor = Math.abs(x) % majorStep === 0 || Math.abs(Math.round(x) % majorStep) === 0;
    const isMid = !isMajor && (Math.abs(x) % midStep === 0 || Math.abs(Math.round(x) % midStep) === 0);

    let tickLen = 22;
    if (isMajor) tickLen = 52;
    else if (isMid) tickLen = 34;

    const yStart = tickBaseY;
    const yEnd = isRear ? tickBaseY - tickLen : tickBaseY + tickLen;

    ctx.beginPath();
    ctx.moveTo(u, yStart);
    ctx.lineTo(u, yEnd);

    if (isMajor) {
      ctx.strokeStyle = (Math.abs(x) < 0.001) ? "#00f0ff" : "#38bdf8";
      ctx.lineWidth = (Math.abs(x) < 0.001) ? 5 : 3;
    } else if (isMid) {
      ctx.strokeStyle = "#94a3b8";
      ctx.lineWidth = 2;
    } else {
      ctx.strokeStyle = "#475569";
      ctx.lineWidth = 1.2;
    }
    ctx.stroke();

    // Major numbers
    if (isMajor) {
      const isZero = Math.abs(x) < 0.001;
      const primaryText = isZero ? "0 (CTR)" : (x > 0 ? `+${Math.round(x)}` : `${Math.round(x)}`);
      const cornerMm = Math.round(x + w / 2);
      const subText = `(${cornerMm})`;

      ctx.save();
      ctx.textAlign = "center";

      if (!isRear) {
        ctx.fillStyle = isZero ? "#00f0ff" : "#ffffff";
        ctx.font = "bold 28px Consolas, monospace";
        ctx.textBaseline = "top";
        ctx.fillText(primaryText, u, 56);

        ctx.fillStyle = "#94a3b8";
        ctx.font = "16px Consolas, monospace";
        ctx.fillText(subText, u, 92);
      } else {
        ctx.fillStyle = isZero ? "#00f0ff" : "#ffffff";
        ctx.font = "bold 28px Consolas, monospace";
        ctx.textBaseline = "bottom";
        ctx.fillText(primaryText, u, canvas.height - 56);

        ctx.fillStyle = "#94a3b8";
        ctx.font = "16px Consolas, monospace";
        ctx.fillText(subText, u, canvas.height - 92);
      }
      ctx.restore();
    }
  }

  // End Indicators
  ctx.font = "bold 18px Consolas, monospace";
  ctx.fillStyle = "#38bdf8";
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillText("◄ -X (mm)", 14, canvas.height / 2);
  ctx.textAlign = "right";
  ctx.fillText("+X (mm) ►", canvas.width - 14, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  if (renderer && renderer.capabilities) {
    tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  }
  return tex;
}

// Generate High-Precision Metric Ruler Texture for Y-Axis (Left or Right Edge)
function createRulerTextureY(l, isRight = false) {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 2048;
  const ctx = canvas.getContext("2d");

  const { minorStep, midStep, majorStep } = getRulerStepInfo(l);
  const yMin = -l / 2;

  // Background
  ctx.fillStyle = "#111319";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = "#181c25";
  ctx.fillRect(3, 0, canvas.width - 6, canvas.height);

  // Baseline on side facing bed interior
  const tickBaseX = isRight ? 2 : canvas.width - 2;
  ctx.fillStyle = "#38bdf8";
  ctx.fillRect(isRight ? 2 : canvas.width - 5, 0, 3, canvas.height);

  ctx.fillStyle = "#334155";
  ctx.fillRect(isRight ? canvas.width - 3 : 1, 0, 2, canvas.height);

  const startY = Math.ceil(yMin / minorStep) * minorStep;
  const endY = Math.floor((l / 2) / minorStep) * minorStep;

  for (let y = startY; y <= endY; y += minorStep) {
    // In Three.js PlaneGeometry, +Y is top of plane (row 0 in canvas), -Y is bottom (row 2048).
    const v = ((l / 2 - y) / l) * canvas.height;
    if (v < 4 || v > canvas.height - 4) continue;

    const isMajor = Math.abs(y) % majorStep === 0 || Math.abs(Math.round(y) % majorStep) === 0;
    const isMid = !isMajor && (Math.abs(y) % midStep === 0 || Math.abs(Math.round(y) % midStep) === 0);

    let tickLen = 22;
    if (isMajor) tickLen = 52;
    else if (isMid) tickLen = 34;

    const xStart = tickBaseX;
    const xEnd = isRight ? tickBaseX + tickLen : tickBaseX - tickLen;

    ctx.beginPath();
    ctx.moveTo(xStart, v);
    ctx.lineTo(xEnd, v);

    if (isMajor) {
      ctx.strokeStyle = (Math.abs(y) < 0.001) ? "#00f0ff" : "#38bdf8";
      ctx.lineWidth = (Math.abs(y) < 0.001) ? 5 : 3;
    } else if (isMid) {
      ctx.strokeStyle = "#94a3b8";
      ctx.lineWidth = 2;
    } else {
      ctx.strokeStyle = "#475569";
      ctx.lineWidth = 1.2;
    }
    ctx.stroke();

    // Major numbers
    if (isMajor) {
      const isZero = Math.abs(y) < 0.001;
      const primaryText = isZero ? "0" : (y > 0 ? `+${Math.round(y)}` : `${Math.round(y)}`);
      const cornerMm = Math.round(y + l / 2);
      const subText = `(${cornerMm})`;

      const textCenterX = isRight ? 86 : 42;

      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      ctx.fillStyle = isZero ? "#00f0ff" : "#ffffff";
      ctx.font = "bold 24px Consolas, monospace";
      ctx.fillText(primaryText, textCenterX, v - 10);

      ctx.fillStyle = "#94a3b8";
      ctx.font = "16px Consolas, monospace";
      ctx.fillText(subText, textCenterX, v + 12);
      ctx.restore();
    }
  }

  // End Indicators
  ctx.font = "bold 16px Consolas, monospace";
  ctx.fillStyle = "#38bdf8";
  ctx.textAlign = "center";
  ctx.fillText("▲ +Y", canvas.width / 2, 28);
  ctx.fillText("▼ -Y", canvas.width / 2, canvas.height - 24);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  if (renderer && renderer.capabilities) {
    tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  }
  return tex;
}

// High-Contrast Coordinate Badge for Internal Bed Grid
function createCoordinateBadgeMesh(text, color = "#ffffff", bgColor = "rgba(15, 23, 42, 0.88)") {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 48;
  const ctx = canvas.getContext("2d");

  ctx.fillStyle = bgColor;
  drawCanvasRoundRect(ctx, 2, 2, canvas.width - 4, canvas.height - 4, 10);
  ctx.fill();

  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.stroke();

  ctx.fillStyle = color;
  ctx.font = "bold 24px Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;

  const geo = new THREE.PlaneGeometry(16, 6);
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false });
  return new THREE.Mesh(geo, mat);
}

// Center Work Zero (G54) Bullseye & Origin Reticle
function createCenterOriginTarget() {
  const group = new THREE.Group();

  const ringPts = [];
  // Outer circle 12mm
  for (let a = 0; a <= 36; a++) {
    const theta = (a / 36) * Math.PI * 2;
    ringPts.push(new THREE.Vector3(12 * Math.cos(theta), 12 * Math.sin(theta), 0.06));
  }
  // Inner circle 5mm
  for (let a = 0; a <= 36; a++) {
    const theta = (a / 36) * Math.PI * 2;
    ringPts.push(new THREE.Vector3(5 * Math.cos(theta), 5 * Math.sin(theta), 0.06));
  }
  const ringGeo = new THREE.BufferGeometry().setFromPoints(ringPts);
  const ringMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, linewidth: 2.2 });
  group.add(new THREE.Line(ringGeo, ringMat));

  // Crosshair lines
  const crossPts = [
    new THREE.Vector3(-18, 0, 0.06), new THREE.Vector3(18, 0, 0.06),
    new THREE.Vector3(0, -18, 0.06), new THREE.Vector3(0, 18, 0.06)
  ];
  const crossGeo = new THREE.BufferGeometry().setFromPoints(crossPts);
  const crossMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, linewidth: 2.5 });
  group.add(new THREE.LineSegments(crossGeo, crossMat));

  // Center Badge
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "rgba(10, 15, 25, 0.92)";
  drawCanvasRoundRect(ctx, 2, 2, canvas.width - 4, canvas.height - 4, 12);
  ctx.fill();
  ctx.strokeStyle = "#00f0ff";
  ctx.lineWidth = 3;
  ctx.stroke();

  ctx.fillStyle = "#00f0ff";
  ctx.font = "bold 22px Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("⨁ (0, 0) WORK ZERO", canvas.width / 2, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  const badgeGeo = new THREE.PlaneGeometry(36, 9);
  const badgeMat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false });
  const badge = new THREE.Mesh(badgeGeo, badgeMat);
  badge.position.set(0, -16, 0.06);
  group.add(badge);

  return group;
}

// Machine Corner Origin (0,0 Spoilboard Corner) Marker
function createCornerHomeMarker(w, l) {
  const group = new THREE.Group();
  const cx = -w / 2;
  const cy = -l / 2;

  const lPts = [
    new THREE.Vector3(cx, cy, 0.06), new THREE.Vector3(cx + 25, cy, 0.06),
    new THREE.Vector3(cx, cy, 0.06), new THREE.Vector3(cx, cy + 25, 0.06)
  ];
  const lGeo = new THREE.BufferGeometry().setFromPoints(lPts);
  const lMat = new THREE.LineBasicMaterial({ color: 0xf59e0b, linewidth: 3 });
  group.add(new THREE.LineSegments(lGeo, lMat));

  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "rgba(24, 18, 10, 0.92)";
  drawCanvasRoundRect(ctx, 2, 2, canvas.width - 4, canvas.height - 4, 12);
  ctx.fill();
  ctx.strokeStyle = "#f59e0b";
  ctx.lineWidth = 3;
  ctx.stroke();

  ctx.fillStyle = "#f59e0b";
  ctx.font = "bold 20px Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("MACHINE (0, 0)", canvas.width / 2, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  const badgeGeo = new THREE.PlaneGeometry(34, 8.5);
  const badgeMat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false });
  const badge = new THREE.Mesh(badgeGeo, badgeMat);
  badge.position.set(cx + 22, cy + 22, 0.06);
  group.add(badge);

  return group;
}

// Assemble Complete Bed Positioning Rulers & Numbers System
function createBedPositioningRulers(machine) {
  const w = machine ? machine.bed_width_x : 300;
  const l = machine ? machine.bed_length_y : 180;

  const rulersGroup = new THREE.Group();
  rulersGroup.name = "bedNumbersGroup";

  const rulerThickness = 12;

  // 1. Front X-Axis Ruler Strip
  const frontTex = createRulerTextureX(w, false);
  const frontGeo = new THREE.PlaneGeometry(w, rulerThickness);
  const frontMat = new THREE.MeshBasicMaterial({ map: frontTex, transparent: true });
  const frontMesh = new THREE.Mesh(frontGeo, frontMat);
  frontMesh.position.set(0, -l / 2 + rulerThickness / 2, 0.05);
  rulersGroup.add(frontMesh);

  // 2. Left Y-Axis Ruler Strip
  const leftTex = createRulerTextureY(l, false);
  const leftGeo = new THREE.PlaneGeometry(rulerThickness, l);
  const leftMat = new THREE.MeshBasicMaterial({ map: leftTex, transparent: true });
  const leftMesh = new THREE.Mesh(leftGeo, leftMat);
  leftMesh.position.set(-w / 2 + rulerThickness / 2, 0, 0.05);
  rulersGroup.add(leftMesh);

  // 3. Rear X-Axis Ruler Strip
  const rearTex = createRulerTextureX(w, true);
  const rearGeo = new THREE.PlaneGeometry(w, rulerThickness);
  const rearMat = new THREE.MeshBasicMaterial({ map: rearTex, transparent: true });
  const rearMesh = new THREE.Mesh(rearGeo, rearMat);
  rearMesh.position.set(0, l / 2 - rulerThickness / 2, 0.05);
  rulersGroup.add(rearMesh);

  // 4. Right Y-Axis Ruler Strip
  const rightTex = createRulerTextureY(l, true);
  const rightGeo = new THREE.PlaneGeometry(rulerThickness, l);
  const rightMat = new THREE.MeshBasicMaterial({ map: rightTex, transparent: true });
  const rightMesh = new THREE.Mesh(rightGeo, rightMat);
  rightMesh.position.set(w / 2 - rulerThickness / 2, 0, 0.05);
  rulersGroup.add(rightMesh);

  // 5. Center Work Origin (G54) Bullseye
  rulersGroup.add(createCenterOriginTarget());

  // 6. Spoilboard Corner Machine Origin Marker
  rulersGroup.add(createCornerHomeMarker(w, l));

  // 7. Internal Crosshair Coordinate Badges along X=0 and Y=0
  const { majorStep } = getRulerStepInfo(Math.max(w, l));

  // Along X-Axis (placed at Y = +7mm above X line)
  const maxBadgeX = w / 2 - 25;
  for (let x = -Math.floor(maxBadgeX / majorStep) * majorStep; x <= maxBadgeX; x += majorStep) {
    if (Math.abs(x) < 0.001) continue; // Skip origin (has center target)
    const signStr = x > 0 ? `+${x}` : `${x}`;
    const badge = createCoordinateBadgeMesh(`X:${signStr}`, "#38bdf8");
    badge.position.set(x, 7.5, 0.06);
    rulersGroup.add(badge);
  }

  // Along Y-Axis (placed at X = +7mm right of Y line)
  const maxBadgeY = l / 2 - 25;
  for (let y = -Math.floor(maxBadgeY / majorStep) * majorStep; y <= maxBadgeY; y += majorStep) {
    if (Math.abs(y) < 0.001) continue; // Skip origin
    const signStr = y > 0 ? `+${y}` : `${y}`;
    const badge = createCoordinateBadgeMesh(`Y:${signStr}`, "#34d399");
    badge.position.set(7.5, y, 0.06);
    rulersGroup.add(badge);
  }

  return rulersGroup;
}

// Build Authentic CNC Spoilboard Bed based on Machine Profile
function createBedGrid(machine) {
  if (bedGrid) scene.remove(bedGrid);

  const w = machine ? machine.bed_width_x : 300;
  const l = machine ? machine.bed_length_y : 180;
  const maxZ = machine ? machine.max_z_travel : 45;
  const layout = machine ? machine.bed_layout : "aluminum_tslot";
  const name = machine ? machine.name : "Genmitsu 3018-PROVer";

  const gridGroup = new THREE.Group();

  // 1. Spoilboard Base Plate (Material finish adapts to machine style)
  let plateMat;
  if (layout === "cast_iron_tslot") {
    // Heavy Cast-Iron Industrial Machine Table (Haas VF-2, Tormach 1100M, DMG MORI, Brother)
    plateMat = new THREE.MeshStandardMaterial({
      color: 0x17191e,
      roughness: 0.38,
      metalness: 0.85
    });
  } else if (layout === "vacuum_chuck_bed") {
    // Precision Vacuum Bed Plate with Integrated Suction Zones (Datron neo)
    plateMat = new THREE.MeshStandardMaterial({
      color: 0x192130,
      roughness: 0.32,
      metalness: 0.78
    });
  } else if (layout === "mdf_hybrid_spoilboard") {
    // Warm MDF fiberboard (Shapeoko 4, Avid CNC)
    plateMat = new THREE.MeshStandardMaterial({
      color: 0x3d3024,
      roughness: 0.9,
      metalness: 0.05
    });
  } else if (layout === "precision_tooling") {
    // Precision ground anodized tooling plate (Nomad 3, Bantam, Carvera)
    plateMat = new THREE.MeshStandardMaterial({
      color: 0x1f2329,
      roughness: 0.35,
      metalness: 0.8
    });
  } else if (layout === "wasteboard_ruler_grid") {
    // Screen-printed composite wasteboard (X-Carve)
    plateMat = new THREE.MeshStandardMaterial({
      color: 0x2a241f,
      roughness: 0.95,
      metalness: 0.05
    });
  } else {
    // Standard Extruded Aluminum T-Slot Plate (Genmitsu 3018, 4030)
    plateMat = new THREE.MeshStandardMaterial({
      color: 0x23272d,
      roughness: 0.45,
      metalness: 0.75
    });
  }

  const plateGeo = new THREE.PlaneGeometry(w, l);
  const plate = new THREE.Mesh(plateGeo, plateMat);
  plate.position.set(0, 0, -0.2);
  gridGroup.add(plate);

  // 2. Main Dimension Grid: Exact rectangular boundaries matching (w x l)
  const gridLinesPts = [];
  const gridColors = [];
  const colMajor = new THREE.Color(0x38bdf8); // 50mm major grid lines
  const colMinor = new THREE.Color(0x272b35); // 10mm minor grid lines
  const colZero = new THREE.Color(0x00f0ff);  // Center zero crosshairs

  const xHalf = w / 2;
  const yHalf = l / 2;

  // Grid lines along Y (spanning from -l/2 to +l/2 at constant X)
  for (let x = -xHalf; x <= xHalf + 0.001; x += 10) {
    const isZero = Math.abs(x) < 0.1;
    const isMajor = Math.abs(Math.round(x) % 50) < 0.1;
    const c = isZero ? colZero : (isMajor ? colMajor : colMinor);
    gridLinesPts.push(new THREE.Vector3(x, -yHalf, 0.02), new THREE.Vector3(x, yHalf, 0.02));
    gridColors.push(c.r, c.g, c.b, c.r, c.g, c.b);
  }

  // Grid lines along X (spanning from -w/2 to +w/2 at constant Y)
  for (let y = -yHalf; y <= yHalf + 0.001; y += 10) {
    const isZero = Math.abs(y) < 0.1;
    const isMajor = Math.abs(Math.round(y) % 50) < 0.1;
    const c = isZero ? colZero : (isMajor ? colMajor : colMinor);
    gridLinesPts.push(new THREE.Vector3(-xHalf, y, 0.02), new THREE.Vector3(xHalf, y, 0.02));
    gridColors.push(c.r, c.g, c.b, c.r, c.g, c.b);
  }

  const gridGeo = new THREE.BufferGeometry().setFromPoints(gridLinesPts);
  gridGeo.setAttribute("color", new THREE.Float32BufferAttribute(gridColors, 3));
  const gridMat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.85 });
  gridGroup.add(new THREE.LineSegments(gridGeo, gridMat));

  // 3. Bed-Specific Clamping Layout Features (T-Tracks, Fixture Holes, Dogholes)
  const featuresGroup = new THREE.Group();
  featuresGroup.name = "bedFeaturesGroup";

  if (layout === "cast_iron_tslot") {
    // Heavy Industrial Cast-Iron Ground Table: Deep T-Slots & Coolant Perimeter
    const pitch = (machine && machine.t_slot_pitch_mm) ? machine.t_slot_pitch_mm : 75;
    const trackCount = Math.max(3, Math.floor((l - 60) / pitch));
    const startY = -(trackCount * pitch) / 2 + pitch / 2;

    const trackLinePts = [];
    const slotMeshGroup = new THREE.Group();

    for (let i = 0; i < trackCount; i++) {
      const y = startY + i * pitch;
      const slotGeo = new THREE.PlaneGeometry(w - 10, 14);
      const slotMat = new THREE.MeshBasicMaterial({ color: 0x090a0d });
      const slotMesh = new THREE.Mesh(slotGeo, slotMat);
      slotMesh.position.set(0, y, -0.08);
      slotMeshGroup.add(slotMesh);

      // Bright ground T-slot edges
      trackLinePts.push(new THREE.Vector3(-w / 2 + 5, y - 7, 0.05));
      trackLinePts.push(new THREE.Vector3(w / 2 - 5, y - 7, 0.05));
      trackLinePts.push(new THREE.Vector3(-w / 2 + 5, y + 7, 0.05));
      trackLinePts.push(new THREE.Vector3(w / 2 - 5, y + 7, 0.05));
    }

    // Industrial Coolant Drainage Perimeter Trough
    const drainGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-w / 2 + 4, -l / 2 + 4, 0.04), new THREE.Vector3(w / 2 - 4, -l / 2 + 4, 0.04),
      new THREE.Vector3(w / 2 - 4, -l / 2 + 4, 0.04), new THREE.Vector3(w / 2 - 4, l / 2 - 4, 0.04),
      new THREE.Vector3(w / 2 - 4, l / 2 - 4, 0.04), new THREE.Vector3(-w / 2 + 4, l / 2 - 4, 0.04),
      new THREE.Vector3(-w / 2 + 4, l / 2 - 4, 0.04), new THREE.Vector3(-w / 2 + 4, -l / 2 + 4, 0.04)
    ]);
    const drainMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, opacity: 0.6, transparent: true });
    featuresGroup.add(new THREE.LineSegments(drainGeo, drainMat));

    featuresGroup.add(slotMeshGroup);
    if (trackLinePts.length > 0) {
      const trackLinesGeo = new THREE.BufferGeometry().setFromPoints(trackLinePts);
      const trackLinesMat = new THREE.LineBasicMaterial({ color: 0x94a3b8, linewidth: 2 });
      featuresGroup.add(new THREE.LineSegments(trackLinesGeo, trackLinesMat));
    }
  } else if (layout === "vacuum_chuck_bed") {
    // High-Speed Vacuum Chuck Bed (Datron neo): Grid of suction ports & gasket grooves
    const vPitch = 30.0;
    const cols = Math.floor((w - 20) / vPitch);
    const rows = Math.floor((l - 20) / vPitch);
    const startX = -(cols * vPitch) / 2 + vPitch / 2;
    const startY = -(rows * vPitch) / 2 + vPitch / 2;

    const vacPts = [];
    for (let c = 0; c < cols; c++) {
      const vx = startX + c * vPitch;
      for (let r = 0; r < rows; r++) {
        const vy = startY + r * vPitch;
        // Circular vacuum suction hole
        for (let a = 0; a <= 8; a++) {
          const theta = (a / 8) * Math.PI * 2;
          vacPts.push(new THREE.Vector3(vx + 2.0 * Math.cos(theta), vy + 2.0 * Math.sin(theta), 0.05));
        }
      }
    }
    if (vacPts.length > 0) {
      const vacGeo = new THREE.BufferGeometry().setFromPoints(vacPts);
      const vacMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, opacity: 0.65, transparent: true });
      featuresGroup.add(new THREE.Line(vacGeo, vacMat));
    }
  } else if (layout === "aluminum_tslot" || layout === "mdf_hybrid_spoilboard") {
    // Parallel Aluminum T-Tracks along X
    const pitch = (machine && machine.t_slot_pitch_mm) ? machine.t_slot_pitch_mm : 25;
    const trackCount = Math.floor(l / pitch);
    const startY = -(trackCount * pitch) / 2 + pitch / 2;

    const trackLinePts = [];
    const slotMeshGroup = new THREE.Group();

    for (let i = 0; i < trackCount; i++) {
      const y = startY + i * pitch;

      // Dark recessed T-track channel
      const slotGeo = new THREE.PlaneGeometry(w - 4, 6);
      const slotMat = new THREE.MeshBasicMaterial({ color: 0x111215 });
      const slotMesh = new THREE.Mesh(slotGeo, slotMat);
      slotMesh.position.set(0, y, -0.1);
      slotMeshGroup.add(slotMesh);

      // Bright extruded metallic lips
      trackLinePts.push(new THREE.Vector3(-w / 2 + 2, y - 3, 0.05));
      trackLinePts.push(new THREE.Vector3(w / 2 - 2, y - 3, 0.05));
      trackLinePts.push(new THREE.Vector3(-w / 2 + 2, y + 3, 0.05));
      trackLinePts.push(new THREE.Vector3(w / 2 - 2, y + 3, 0.05));
    }

    featuresGroup.add(slotMeshGroup);

    if (trackLinePts.length > 0) {
      const trackLinesGeo = new THREE.BufferGeometry().setFromPoints(trackLinePts);
      const trackLinesMat = new THREE.LineBasicMaterial({ color: 0x64748b, linewidth: 1.5 });
      featuresGroup.add(new THREE.LineSegments(trackLinesGeo, trackLinesMat));
    }
  }

  if (layout === "precision_tooling" || layout === "mdf_hybrid_spoilboard") {
    // Matrix of M6 Threaded Fixture Holes / Dogholes
    const holePitch = 25.0;
    const cols = Math.floor(w / holePitch);
    const rows = Math.floor(l / holePitch);
    const startX = -(cols * holePitch) / 2 + holePitch / 2;
    const startY = -(rows * holePitch) / 2 + holePitch / 2;

    const holePts = [];
    for (let c = 0; c < cols; c++) {
      const hx = startX + c * holePitch;
      for (let r = 0; r < rows; r++) {
        const hy = startY + r * holePitch;
        // Circle for M6 threaded insert
        for (let a = 0; a <= 12; a++) {
          const theta = (a / 12) * Math.PI * 2;
          holePts.push(new THREE.Vector3(hx + 2.5 * Math.cos(theta), hy + 2.5 * Math.sin(theta), 0.05));
        }
      }
    }
    if (holePts.length > 0) {
      const holeGeo = new THREE.BufferGeometry().setFromPoints(holePts);
      const holeMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, opacity: 0.45, transparent: true });
      featuresGroup.add(new THREE.Line(holeGeo, holeMat));
    }

    if (layout === "precision_tooling") {
      // Corner alignment bracket (L-fence) at bottom-left corner
      const fenceGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-w / 2 + 10, -l / 2 + 10, 0.2),
        new THREE.Vector3(-w / 2 + 50, -l / 2 + 10, 0.2),
        new THREE.Vector3(-w / 2 + 10, -l / 2 + 10, 0.2),
        new THREE.Vector3(-w / 2 + 10, -l / 2 + 50, 0.2)
      ]);
      const fenceMat = new THREE.LineBasicMaterial({ color: 0xf59e0b, linewidth: 2.5 });
      featuresGroup.add(new THREE.LineSegments(fenceGeo, fenceMat));
    }
  }

  gridGroup.add(featuresGroup);
  bedFeaturesMesh = featuresGroup;

  // 4. Machine Model Nameplate Badge on Front Lip
  const badgeTex = createMachineNameplateTexture(name, `${w.toFixed(0)} × ${l.toFixed(0)} × ${maxZ.toFixed(0)} mm`);
  const badgeGeo = new THREE.PlaneGeometry(Math.min(180, w * 0.75), 24);
  const badgeMat = new THREE.MeshBasicMaterial({ map: badgeTex, transparent: true });
  const badgeMesh = new THREE.Mesh(badgeGeo, badgeMat);
  badgeMesh.position.set(0, -l / 2 - 16, 0.1);
  gridGroup.add(badgeMesh);

  // 5. Build Volume Travel Cage (Wireframe Box from Z=0 to Z=maxZ)
  const cageGroup = new THREE.Group();
  cageGroup.name = "travelCageGroup";

  const cagePts = [
    // Bottom perimeter (Z=0)
    new THREE.Vector3(-w / 2, -l / 2, 0), new THREE.Vector3(w / 2, -l / 2, 0),
    new THREE.Vector3(w / 2, -l / 2, 0), new THREE.Vector3(w / 2, l / 2, 0),
    new THREE.Vector3(w / 2, l / 2, 0), new THREE.Vector3(-w / 2, l / 2, 0),
    new THREE.Vector3(-w / 2, l / 2, 0), new THREE.Vector3(-w / 2, -l / 2, 0),

    // Top perimeter (Z=maxZ)
    new THREE.Vector3(-w / 2, -l / 2, maxZ), new THREE.Vector3(w / 2, -l / 2, maxZ),
    new THREE.Vector3(w / 2, -l / 2, maxZ), new THREE.Vector3(w / 2, l / 2, maxZ),
    new THREE.Vector3(w / 2, l / 2, maxZ), new THREE.Vector3(-w / 2, l / 2, maxZ),
    new THREE.Vector3(-w / 2, l / 2, maxZ), new THREE.Vector3(-w / 2, -l / 2, maxZ),

    // 4 Corner vertical pillars
    new THREE.Vector3(-w / 2, -l / 2, 0), new THREE.Vector3(-w / 2, -l / 2, maxZ),
    new THREE.Vector3(w / 2, -l / 2, 0), new THREE.Vector3(w / 2, -l / 2, maxZ),
    new THREE.Vector3(w / 2, l / 2, 0), new THREE.Vector3(w / 2, l / 2, maxZ),
    new THREE.Vector3(-w / 2, l / 2, 0), new THREE.Vector3(-w / 2, l / 2, maxZ)
  ];

  const cageGeo = new THREE.BufferGeometry().setFromPoints(cagePts);
  const cageMat = new THREE.LineDashedMaterial({
    color: 0x00f0ff,
    linewidth: 1,
    scale: 1,
    dashSize: 4,
    gapSize: 3,
    transparent: true,
    opacity: 0.35
  });
  const cageLines = new THREE.LineSegments(cageGeo, cageMat);
  cageLines.computeLineDistances();
  cageGroup.add(cageLines);

  // Solid Corner Brackets (for high-tech CAD aesthetic)
  const bracketPts = [];
  const bLen = Math.min(15, Math.min(w, l) * 0.1);
  const corners = [
    [-w / 2, -l / 2], [w / 2, -l / 2],
    [w / 2, l / 2], [-w / 2, l / 2]
  ];
  corners.forEach(([cx, cy]) => {
    const sgnX = cx < 0 ? 1 : -1;
    const sgnY = cy < 0 ? 1 : -1;
    // Bottom brackets
    bracketPts.push(new THREE.Vector3(cx, cy, 0), new THREE.Vector3(cx + sgnX * bLen, cy, 0));
    bracketPts.push(new THREE.Vector3(cx, cy, 0), new THREE.Vector3(cx, cy + sgnY * bLen, 0));
    bracketPts.push(new THREE.Vector3(cx, cy, 0), new THREE.Vector3(cx, cy, bLen));
    // Top brackets
    bracketPts.push(new THREE.Vector3(cx, cy, maxZ), new THREE.Vector3(cx + sgnX * bLen, cy, maxZ));
    bracketPts.push(new THREE.Vector3(cx, cy, maxZ), new THREE.Vector3(cx, cy + sgnY * bLen, maxZ));
    bracketPts.push(new THREE.Vector3(cx, cy, maxZ), new THREE.Vector3(cx, cy, maxZ - bLen));
  });
  const bracketGeo = new THREE.BufferGeometry().setFromPoints(bracketPts);
  const bracketMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, linewidth: 2 });
  cageGroup.add(new THREE.LineSegments(bracketGeo, bracketMat));

  gridGroup.add(cageGroup);
  travelCageMesh = cageGroup;

  // 6. Coordinate Axes Helper (Red=X, Green=Y, Blue=Z centered at (0, 0, 0))
  const axesLen = Math.max(30, Math.min(60, w * 0.2));
  const axes = new THREE.AxesHelper(axesLen);
  axes.renderOrder = 2;
  gridGroup.add(axes);

  // 7. Metric Spoilboard Positioning Rulers & Numbers System
  const rulersGroup = createBedPositioningRulers(machine);
  gridGroup.add(rulersGroup);
  bedNumbersMesh = rulersGroup;

  bedGrid = gridGroup;
  scene.add(bedGrid);

  // Sync checkboxes
  const chkCage = document.getElementById("chk-show-travel-cage");
  if (chkCage) travelCageMesh.visible = chkCage.checked;
  const chkFeatures = document.getElementById("chk-show-bed-features");
  if (chkFeatures) bedFeaturesMesh.visible = chkFeatures.checked;
  const chkNumbers = document.getElementById("chk-show-bed-numbers");
  if (chkNumbers) bedNumbersMesh.visible = chkNumbers.checked;
}

// Load Machine Profiles from backend and populate UI
async function loadMachineProfiles() {
  try {
    const res = await fetch("/api/machines");
    const data = await res.json();
    if (data.success) {
      machinesList = data.machines;
      currentMachine = data.active_machine || machinesList[0];

      // Populate both select dropdowns (header and sidebar) with categorized optgroups
      const selHeader = document.getElementById("machine-select");
      const selSidebar = document.getElementById("machine-select-sidebar");
      [selHeader, selSidebar].forEach((sel) => {
        if (!sel) return;
        sel.innerHTML = "";

        const industrialIds = ["tormach_1100m", "datron_neo", "brother_speedio", "haas_vf2", "dmg_mori_cmx600v", "avid_pro4848"];

        const grpDesktop = document.createElement("optgroup");
        grpDesktop.label = "Desktop & Benchtop CNC";
        const grpIndustrial = document.createElement("optgroup");
        grpIndustrial.label = "Industrial Machining Centers";
        const grpCustom = document.createElement("optgroup");
        grpCustom.label = "Custom Spoilboard";

        machinesList.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.id;
          opt.textContent = `${m.name} (${m.envelope_label})`;
          if (m.id === currentMachine.id) opt.selected = true;

          if (m.id === "custom_mill") {
            grpCustom.appendChild(opt);
          } else if (industrialIds.includes(m.id)) {
            grpIndustrial.appendChild(opt);
          } else {
            grpDesktop.appendChild(opt);
          }
        });

        if (grpDesktop.children.length > 0) sel.appendChild(grpDesktop);
        if (grpIndustrial.children.length > 0) sel.appendChild(grpIndustrial);
        if (grpCustom.children.length > 0) sel.appendChild(grpCustom);
      });

      updateMachineUI(currentMachine);
      createBedGrid(currentMachine);
    }
  } catch (err) {
    console.error("Failed to load machine profiles:", err);
    currentMachine = {
      id: "genmitsu_3018",
      name: "Genmitsu 3018-PROVer",
      bed_width_x: 300,
      bed_length_y: 180,
      max_z_travel: 45,
      bed_layout: "aluminum_tslot",
      spindle_max_rpm: 10000
    };
    createBedGrid(currentMachine);
  }
}

function updateMachineUI(machine) {
  // Synchronize both select dropdowns
  const selHeader = document.getElementById("machine-select");
  if (selHeader && selHeader.value !== machine.id) selHeader.value = machine.id;
  const selSidebar = document.getElementById("machine-select-sidebar");
  if (selSidebar && selSidebar.value !== machine.id) selSidebar.value = machine.id;

  // Header badge (if present)
  const headerName = document.getElementById("header-machine-name");
  if (headerName) headerName.textContent = machine.name;
  const headerBed = document.getElementById("header-machine-bed");
  if (headerBed) headerBed.textContent = `${machine.bed_width_x.toFixed(0)}×${machine.bed_length_y.toFixed(0)} mm`;

  // Sidebar stats
  const statBed = document.getElementById("machine-stat-bed");
  if (statBed) statBed.textContent = `${machine.bed_width_x.toFixed(0)} × ${machine.bed_length_y.toFixed(0)} mm`;
  const statZ = document.getElementById("machine-stat-z");
  if (statZ) statZ.textContent = `${machine.max_z_travel.toFixed(0)} mm`;
  const statLayout = document.getElementById("machine-stat-layout");
  if (statLayout) {
    const layoutNames = {
      aluminum_tslot: "Aluminum T-Slot",
      mdf_hybrid_spoilboard: "MDF / T-Track Hybrid",
      precision_tooling: "Precision Tooling Plate",
      wasteboard_ruler_grid: "Wasteboard Metric Grid",
      cast_iron_tslot: "Cast-Iron Precision Table",
      vacuum_chuck_bed: "High-Speed Vacuum Bed",
      custom_bed: "Custom Spoilboard"
    };
    statLayout.textContent = layoutNames[machine.bed_layout] || machine.bed_layout;
  }
  const statSpindle = document.getElementById("machine-stat-spindle");
  if (statSpindle) statSpindle.textContent = `${machine.spindle_max_rpm.toLocaleString()} RPM`;

  // Speeds & Feeds input defaults
  const spindleInput = document.getElementById("spindle-rpm");
  if (spindleInput && parseInt(spindleInput.value) > machine.spindle_max_rpm) {
    spindleInput.value = machine.spindle_max_rpm;
  }

  // Show/hide custom controls
  const customControls = document.getElementById("custom-machine-controls");
  if (customControls) {
    customControls.style.display = machine.id === "custom_mill" ? "block" : "none";
  }

  updateStockClampingCard();
}

async function onMachineSelectChanged(selectedId) {
  const selHeader = document.getElementById("machine-select");
  const selSidebar = document.getElementById("machine-select-sidebar");
  const machineId = selectedId || (selHeader ? selHeader.value : (selSidebar ? selSidebar.value : "genmitsu_3018"));

  // Sync both dropdown values immediately
  if (selHeader && selHeader.value !== machineId) selHeader.value = machineId;
  if (selSidebar && selSidebar.value !== machineId) selSidebar.value = machineId;

  const customControls = document.getElementById("custom-machine-controls");
  if (customControls) {
    customControls.style.display = machineId === "custom_mill" ? "block" : "none";
  }

  try {
    const res = await fetch("/api/machines/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machine_id: machineId })
    });
    const data = await res.json();
    if (data.success) {
      currentMachine = data.active_machine;
      updateMachineUI(currentMachine);
      createBedGrid(currentMachine);
      setCameraView("iso");
      setStatus(`Active Machine Profile: ${currentMachine.name} (${currentMachine.bed_width_x} × ${currentMachine.bed_length_y} × ${currentMachine.max_z_travel} mm). Spoilboard updated.`, "ready");
    }
  } catch (err) {
    console.error("Machine selection error:", err);
  }
}

async function applyCustomBedDimensions() {
  const w = parseFloat(document.getElementById("custom-bed-w").value || 400);
  const l = parseFloat(document.getElementById("custom-bed-l").value || 400);
  const z = parseFloat(document.getElementById("custom-bed-z").value || 100);
  const rpm = parseInt(document.getElementById("custom-bed-rpm").value || 16000);

  try {
    const res = await fetch("/api/machines/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        machine_id: "custom_mill",
        bed_width_x: w,
        bed_length_y: l,
        max_z_travel: z,
        spindle_max_rpm: rpm
      })
    });
    const data = await res.json();
    if (data.success) {
      currentMachine = data.active_machine;
      updateMachineUI(currentMachine);
      createBedGrid(currentMachine);
      setCameraView("iso");
      document.getElementById("status-text").textContent = `Custom bed dimensions applied: ${w} × ${l} × ${z} mm.`;
    }
  } catch (err) {
    console.error("Custom bed update error:", err);
  }
}

function setupMachineControls() {
  const machineSelect = document.getElementById("machine-select");
  if (machineSelect) {
    machineSelect.addEventListener("change", (e) => onMachineSelectChanged(e.target.value));
  }
  const machineSelectSidebar = document.getElementById("machine-select-sidebar");
  if (machineSelectSidebar) {
    machineSelectSidebar.addEventListener("change", (e) => onMachineSelectChanged(e.target.value));
  }

  const btnApplyCustom = document.getElementById("btn-apply-custom-bed");
  if (btnApplyCustom) {
    btnApplyCustom.addEventListener("click", applyCustomBedDimensions);
  }

  const chkCage = document.getElementById("chk-show-travel-cage");
  if (chkCage) {
    chkCage.addEventListener("change", (e) => {
      if (travelCageMesh) travelCageMesh.visible = e.target.checked;
    });
  }

  const chkFeatures = document.getElementById("chk-show-bed-features");
  if (chkFeatures) {
    chkFeatures.addEventListener("change", (e) => {
      if (bedFeaturesMesh) bedFeaturesMesh.visible = e.target.checked;
    });
  }

  const chkNumbers = document.getElementById("chk-show-bed-numbers");
  if (chkNumbers) {
    chkNumbers.addEventListener("change", (e) => {
      if (bedNumbersMesh) bedNumbersMesh.visible = e.target.checked;
    });
  }

  const headerBadge = document.getElementById("header-machine-badge");
  if (headerBadge && machineSelect) {
    headerBadge.addEventListener("click", () => {
      machineSelect.focus();
    });
  }
}

// 3D Cutting Tool representation (Spindle collet + Carbide endmill)
function createToolMesh(diameter = 3.175) {
  const group = new THREE.Group();

  // Carbide endmill shaft (silver cylinder)
  const bitGeo = new THREE.CylinderGeometry(diameter / 2, diameter / 2, 25, 16);
  bitGeo.rotateX(Math.PI / 2);
  const bitMat = new THREE.MeshStandardMaterial({ color: 0xe5e7eb, metalness: 0.9, roughness: 0.2 });
  const bit = new THREE.Mesh(bitGeo, bitMat);
  bit.position.set(0, 0, 12.5);
  group.add(bit);

  // ER11 Spindle Collet (dark collet cone)
  const colletGeo = new THREE.CylinderGeometry(10, 5, 20, 16);
  colletGeo.rotateX(Math.PI / 2);
  const colletMat = new THREE.MeshStandardMaterial({ color: 0x1f2937, metalness: 0.8, roughness: 0.5 });
  const collet = new THREE.Mesh(colletGeo, colletMat);
  collet.position.set(0, 0, 35);
  group.add(collet);

  return group;
}

function updateToolMesh(diameter = 3.175) {
  if (toolMeshGroup) {
    const isVis = toolMeshGroup.visible;
    const pos = toolMeshGroup.position.clone();
    scene.remove(toolMeshGroup);
    toolMeshGroup = createToolMesh(diameter);
    toolMeshGroup.position.copy(pos);
    toolMeshGroup.visible = isVis;
    scene.add(toolMeshGroup);
  }
}

// Crisp cylindrical wireframe silhouette (top ring, bottom ring, 4 quadrant ribs)
function createCylinderStockWireframe(radius, height) {
  const group = new THREE.Group();
  const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });

  const topPts = [];
  const botPts = [];
  const segments = 64;
  for (let i = 0; i <= segments; i++) {
    const theta = (i / segments) * Math.PI * 2;
    const x = radius * Math.cos(theta);
    const y = radius * Math.sin(theta);
    topPts.push(new THREE.Vector3(x, y, height / 2));
    botPts.push(new THREE.Vector3(x, y, -height / 2));
  }
  group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(topPts), lineMat));
  group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(botPts), lineMat));

  const ribPts = [];
  for (let i = 0; i < 4; i++) {
    const theta = (i / 4) * Math.PI * 2;
    const x = radius * Math.cos(theta);
    const y = radius * Math.sin(theta);
    ribPts.push(new THREE.Vector3(x, y, -height / 2));
    ribPts.push(new THREE.Vector3(x, y, height / 2));
  }
  group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ribPts), lineMat));

  return group;
}

// Render Raw Stock Mesh (Box, Plate, Cylinder, Round Plate / Disc, Conforming)
function renderCurrentStockMesh() {
  const shape = document.getElementById("stock-shape") ? document.getElementById("stock-shape").value : "box";
  const w = parseFloat(document.getElementById("stock-w") ? document.getElementById("stock-w").value : 70);
  const l = parseFloat(document.getElementById("stock-l") ? document.getElementById("stock-l").value : 70);
  const t = parseFloat(document.getElementById("stock-t") ? document.getElementById("stock-t").value : 32);
  const dia = parseFloat(document.getElementById("stock-d") ? document.getElementById("stock-d").value : 90);

  let cx = 0;
  let cy = 0;

  const isLocked = document.getElementById("stock-lock-to-model") ? document.getElementById("stock-lock-to-model").checked : true;
  if (isLocked && currentMesh) {
    cx = currentMesh.position.x;
    cy = currentMesh.position.y;
  }

  updateStockMesh(shape, w, l, t, dia, cx, cy);
}

function updateStockMesh(shape, w, l, t, dia, cx, cy) {
  if (stockMesh) scene.remove(stockMesh);

  const mat = new THREE.MeshPhysicalMaterial({
    color: 0x10b981, // Translucent emerald
    transparent: true,
    opacity: 0.28,
    roughness: 0.3,
    transmission: 0.6,
    thickness: 1.0,
    depthWrite: false
  });

  const stockGroup = new THREE.Group();

  if (shape === "cylinder" || shape === "round_plate") {
    const r = dia / 2;
    const geo = new THREE.CylinderGeometry(r, r, t, 64);
    geo.rotateX(Math.PI / 2); // Orient cylinder upright with Z-axis
    const mesh = new THREE.Mesh(geo, mat);
    stockGroup.add(mesh);

    // Clean, crisp CAD cylindrical wireframe (top circle, bottom circle, 4 vertical ribs)
    const wireframe = createCylinderStockWireframe(r, t);
    stockGroup.add(wireframe);

    stockGroup.position.set(cx, cy, t / 2);
  } else if (shape === "conforming" && latestAnalysis && latestAnalysis.silhouette_exterior && latestAnalysis.silhouette_exterior.length > 2) {
    const shape2d = new THREE.Shape();
    latestAnalysis.silhouette_exterior.forEach((p, idx) => {
      if (idx === 0) shape2d.moveTo(p[0], p[1]);
      else shape2d.lineTo(p[0], p[1]);
    });
    shape2d.closePath();
    const geo = new THREE.ExtrudeGeometry(shape2d, { depth: t, bevelEnabled: false });
    const mesh = new THREE.Mesh(geo, mat);
    stockGroup.add(mesh);

    const edges = new THREE.EdgesGeometry(geo);
    const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });
    stockGroup.add(new THREE.LineSegments(edges, lineMat));

    stockGroup.position.set(cx, cy, 0);
  } else {
    // Box or Plate
    const geo = new THREE.BoxGeometry(w, l, t);
    const mesh = new THREE.Mesh(geo, mat);
    stockGroup.add(mesh);

    const edges = new THREE.EdgesGeometry(geo);
    const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });
    stockGroup.add(new THREE.LineSegments(edges, lineMat));

    stockGroup.position.set(cx, cy, t / 2);
  }

  stockMesh = stockGroup;
  scene.add(stockMesh);

  updateStockClampingCard(shape, w, l, dia, cx, cy);
}

// Real-time calculation and display of physical machine spoilboard clamping coordinates
function updateStockClampingCard(shape, w, l, dia, cx, cy) {
  const spanCenterEl = document.getElementById("clamp-span-center");
  const spanCornerEl = document.getElementById("clamp-span-corner");
  if (!spanCenterEl || !spanCornerEl) return;

  const bedW = currentMachine ? currentMachine.bed_width_x : 300;
  const bedL = currentMachine ? currentMachine.bed_length_y : 180;

  if (shape === undefined) {
    const shapeEl = document.getElementById("stock-shape");
    shape = shapeEl ? shapeEl.value : "box";
  }
  if (w === undefined) {
    w = parseFloat(document.getElementById("stock-w")?.value || 70);
    l = parseFloat(document.getElementById("stock-l")?.value || 70);
    dia = parseFloat(document.getElementById("stock-d")?.value || 90);
    cx = (stockMesh && stockMesh.position) ? stockMesh.position.x : 0;
    cy = (stockMesh && stockMesh.position) ? stockMesh.position.y : 0;
  }

  let minX, maxX, minY, maxY;

  if (shape === "cylinder" || shape === "round_plate") {
    const r = dia / 2;
    minX = cx - r;
    maxX = cx + r;
    minY = cy - r;
    maxY = cy + r;
  } else if (shape === "conforming" && latestAnalysis && latestAnalysis.silhouette_exterior && latestAnalysis.silhouette_exterior.length > 2) {
    let pts = latestAnalysis.silhouette_exterior;
    let localMinX = Infinity, localMaxX = -Infinity, localMinY = Infinity, localMaxY = -Infinity;
    pts.forEach(p => {
      if (p[0] < localMinX) localMinX = p[0];
      if (p[0] > localMaxX) localMaxX = p[0];
      if (p[1] < localMinY) localMinY = p[1];
      if (p[1] > localMaxY) localMaxY = p[1];
    });
    const margin = parseFloat(document.getElementById("stock-conforming-margin")?.value || 5.0);
    minX = cx + localMinX - margin;
    maxX = cx + localMaxX + margin;
    minY = cy + localMinY - margin;
    maxY = cy + localMaxY + margin;
  } else {
    // Box or plate
    minX = cx - w / 2;
    maxX = cx + w / 2;
    minY = cy - l / 2;
    maxY = cy + l / 2;
  }

  // Corner relative coordinates (from physical machine spoilboard corner at -bedW/2, -bedL/2)
  const cornerMinX = minX + bedW / 2;
  const cornerMaxX = maxX + bedW / 2;
  const cornerMinY = minY + bedL / 2;
  const cornerMaxY = maxY + bedL / 2;

  const fmtSign = (v) => (v >= 0 ? `+${v.toFixed(1)}` : `${v.toFixed(1)}`);

  spanCenterEl.textContent = `X: [${fmtSign(minX)}, ${fmtSign(maxX)}] Y: [${fmtSign(minY)}, ${fmtSign(maxY)}] mm`;
  spanCornerEl.textContent = `X: [${cornerMinX.toFixed(1)}, ${cornerMaxX.toFixed(1)}] Y: [${cornerMinY.toFixed(1)}, ${cornerMaxY.toFixed(1)}] mm`;

  updateSubHeaderTelemetry();
}

// Update Apple Sub-Header Telemetry (Rest dash: File | Stock | Tool | Speed | Time)
function updateSubHeaderTelemetry() {
  const stockW = document.getElementById("stock-w")?.value || "70";
  const stockL = document.getElementById("stock-l")?.value || "70";
  const stockT = document.getElementById("stock-t")?.value || "30";
  const shape = document.getElementById("stock-shape")?.value || "box";
  const toolVal = document.getElementById("tool-select")?.value || "flat_1_8";
  const feedXy = document.getElementById("feed-xy")?.value || "1200";
  const rpm = document.getElementById("spindle-rpm")?.value || "10000";

  const toolLabels = {
    flat_1_8: '1/8" Flat Endmill (3.175mm)',
    flat_1_4: '1/4" Flat Endmill (6.35mm)',
    flat_6mm: '6.0mm Rougher',
    v_bit_60: '60° V-Bit Engraver'
  };

  const elStock = document.getElementById("telemetry-stock-dim");
  if (elStock) {
    elStock.textContent = shape === "cylinder" ? `Ø${stockW} × ${stockT} mm` : `${stockW}×${stockL}×${stockT} mm`;
  }
  const elTool = document.getElementById("telemetry-tool-name");
  if (elTool) {
    elTool.textContent = toolLabels[toolVal] || toolVal;
  }
  const elSpeed = document.getElementById("telemetry-speed-val");
  if (elSpeed) {
    elSpeed.textContent = `${parseInt(rpm, 10).toLocaleString()} RPM @ ${feedXy} mm/min`;
  }
}

// Keep stock and silhouette outline positions synchronized with model
function syncStockToModelPosition() {
  if (!currentMesh) return;
  const t = parseFloat(document.getElementById("stock-t").value || 30);
  const shape = document.getElementById("stock-shape").value;

  if (stockMesh) {
    if (shape === "conforming") {
      stockMesh.position.set(currentMesh.position.x, currentMesh.position.y, 0);
    } else {
      stockMesh.position.set(currentMesh.position.x, currentMesh.position.y, t / 2);
    }
  }

  if (silhouetteOutlineGroup) {
    silhouetteOutlineGroup.position.set(currentMesh.position.x, currentMesh.position.y, 0);
  }

  document.getElementById("stock-pos").value = `X: ${currentMesh.position.x.toFixed(1)}, Y: ${currentMesh.position.y.toFixed(1)}`;
  updateStockClampingCard();
}

// Load 3D Mesh into Scene
function display3DMesh(vertices, faces, metadata, analysis = null) {
  transformControls.detach();
  while (partMeshGroup.children.length > 0) {
    partMeshGroup.remove(partMeshGroup.children[0]);
  }

  const geometry = new THREE.BufferGeometry();
  const positions = [];
  const indices = [];

  for (let i = 0; i < vertices.length; i++) {
    positions.push(vertices[i][0], vertices[i][1], vertices[i][2]);
  }
  for (let i = 0; i < faces.length; i++) {
    indices.push(faces[i][0], faces[i][1], faces[i][2]);
  }

  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    color: 0xf59e0b, // Amber gold part
    roughness: 0.35,
    metalness: 0.65,
    side: THREE.DoubleSide
  });

  const mesh = new THREE.Mesh(geometry, mat);

  // Position model centered at bed origin (0, 0, 0)
  mesh.position.set(0, 0, 0);

  partMeshGroup.add(mesh);
  currentMesh = mesh;

  // Re-attach gizmo if currently in a transform tool
  if (activeTool !== "select" && activeTool !== "mirror") {
    transformControls.attach(currentMesh);
  }

  // Update UI metadata panel
  document.getElementById("model-name-badge").textContent = metadata.name || "3D Model";
  document.getElementById("model-dim-x").value = metadata.width.toFixed(1);
  document.getElementById("model-dim-y").value = metadata.length.toFixed(1);
  document.getElementById("model-dim-z").value = metadata.height.toFixed(1);
  document.getElementById("model-poly-count").value = metadata.faces_count || faces.length;

  // Update Studio Tab model telemetry pill
  const modelPill = document.getElementById("opt-status-model-pill");
  const modelName = document.getElementById("opt-status-model-name");
  if (modelPill && modelName) {
    modelName.textContent = `${metadata.name || "Model"} (${metadata.width.toFixed(0)}×${metadata.length.toFixed(0)}×${metadata.height.toFixed(0)} mm)`;
    modelPill.style.display = "inline-flex";
  }

  // Reset transform inputs to identity
  resetTransformsUI();

  // Apply or run design analysis
  if (analysis) {
    applyAnalysisResult(analysis, true);
  } else {
    runDesignAnalysis(true);
  }

  setStatus(`Loaded 3D Model: ${metadata.name} (${metadata.width.toFixed(1)} x ${metadata.length.toFixed(1)} x ${metadata.height.toFixed(1)} mm)`, "ready");
}

// Reset UI transform fields
function resetTransformsUI() {
  document.getElementById("tf-pos-x").value = "0.0";
  document.getElementById("tf-pos-y").value = "0.0";
  document.getElementById("tf-pos-z").value = "0.0";
  document.getElementById("tf-rot-x").value = "0";
  document.getElementById("tf-rot-y").value = "0";
  document.getElementById("tf-rot-z").value = "0";
  document.getElementById("tf-scale-x").value = "100";
  document.getElementById("tf-scale-y").value = "100";
  document.getElementById("tf-scale-z").value = "100";
}

// Synchronize mesh transformations -> UI drawer fields
function syncMeshToUI() {
  if (!currentMesh) return;

  const px = currentMesh.position.x;
  const py = currentMesh.position.y;
  const pz = currentMesh.position.z;

  document.getElementById("tf-pos-x").value = px.toFixed(1);
  document.getElementById("tf-pos-y").value = py.toFixed(1);
  document.getElementById("tf-pos-z").value = pz.toFixed(1);

  // Convert radians to degrees
  const rx = THREE.MathUtils.radToDeg(currentMesh.rotation.x);
  const ry = THREE.MathUtils.radToDeg(currentMesh.rotation.y);
  const rz = THREE.MathUtils.radToDeg(currentMesh.rotation.z);

  document.getElementById("tf-rot-x").value = Math.round(rx % 360);
  document.getElementById("tf-rot-y").value = Math.round(ry % 360);
  document.getElementById("tf-rot-z").value = Math.round(rz % 360);

  // Scale in percentage
  document.getElementById("tf-scale-x").value = Math.round(currentMesh.scale.x * 100);
  document.getElementById("tf-scale-y").value = Math.round(currentMesh.scale.y * 100);
  document.getElementById("tf-scale-z").value = Math.round(currentMesh.scale.z * 100);
}

// Synchronize UI drawer fields -> mesh transformations
function syncUIToMesh() {
  if (!currentMesh) return;

  const px = parseFloat(document.getElementById("tf-pos-x").value || 0);
  const py = parseFloat(document.getElementById("tf-pos-y").value || 0);
  const pz = parseFloat(document.getElementById("tf-pos-z").value || 0);

  const rx = THREE.MathUtils.degToRad(parseFloat(document.getElementById("tf-rot-x").value || 0));
  const ry = THREE.MathUtils.degToRad(parseFloat(document.getElementById("tf-rot-y").value || 0));
  const rz = THREE.MathUtils.degToRad(parseFloat(document.getElementById("tf-rot-z").value || 0));

  const sx = parseFloat(document.getElementById("tf-scale-x").value || 100) / 100;
  const sy = parseFloat(document.getElementById("tf-scale-y").value || 100) / 100;
  const sz = parseFloat(document.getElementById("tf-scale-z").value || 100) / 100;

  currentMesh.position.set(px, py, pz);
  currentMesh.rotation.set(rx, ry, rz);
  currentMesh.scale.set(sx, sy, sz);

  if (document.getElementById("stock-lock-to-model").checked) {
    syncStockToModelPosition();
  }
}

// Creality Tool Selector & Drawer Management
function setActiveTool(tool) {
  activeTool = tool;
  const drawer = document.getElementById("transform-drawer");
  const drawerTitle = document.getElementById("drawer-title");
  const contentMove = document.getElementById("drawer-move-content");
  const contentRotate = document.getElementById("drawer-rotate-content");
  const contentScale = document.getElementById("drawer-scale-content");
  const contentMirror = document.getElementById("drawer-mirror-content");

  const buttons = {
    select: document.getElementById("tool-select-btn"),
    translate: document.getElementById("tool-translate-btn"),
    rotate: document.getElementById("tool-rotate-btn"),
    scale: document.getElementById("tool-scale-btn"),
    mirror: document.getElementById("tool-mirror-btn")
  };
  Object.values(buttons).forEach((b) => b && b.classList.remove("active"));
  if (buttons[tool]) buttons[tool].classList.add("active");

  contentMove.style.display = "none";
  contentRotate.style.display = "none";
  contentScale.style.display = "none";
  contentMirror.style.display = "none";

  if (tool === "select") {
    transformControls.detach();
    drawer.style.display = "none";
    document.getElementById("status-text").textContent = "View mode active. Orbit with left click, pan with right click, zoom with wheel.";
    return;
  }

  drawer.style.display = "block";

  if (tool === "translate") {
    drawerTitle.textContent = "Move Tool";
    contentMove.style.display = "flex";
    if (currentMesh) {
      transformControls.setMode("translate");
      transformControls.attach(currentMesh);
    }
  } else if (tool === "rotate") {
    drawerTitle.textContent = "Rotate Tool";
    contentRotate.style.display = "flex";
    if (currentMesh) {
      transformControls.setMode("rotate");
      transformControls.attach(currentMesh);
    }
  } else if (tool === "scale") {
    drawerTitle.textContent = "Scale Tool";
    contentScale.style.display = "flex";
    if (currentMesh) {
      transformControls.setMode("scale");
      transformControls.attach(currentMesh);
    }
  } else if (tool === "mirror") {
    drawerTitle.textContent = "Mirror Tool";
    contentMirror.style.display = "flex";
    transformControls.detach();
  }
}

// Lay Flat Face onto Bed Floor (Z=0)
async function layFlatModel() {
  document.getElementById("status-text").textContent = "Computing optimal flat face orientation (aligning to spoilboard Z=0)...";
  try {
    const res = await fetch("/api/model/layflat", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      display3DMesh(data.vertices, data.faces, data.metadata);
      document.getElementById("status-text").textContent = "Model laid flat against spoilboard bed (Z=0).";
    } else {
      alert("Lay flat failed: " + (data.error || "Unknown error"));
    }
  } catch (err) {
    console.error("Lay flat error:", err);
  }
}

function centerModelInStock() {
  document.getElementById("tf-pos-x").value = "0.0";
  document.getElementById("tf-pos-y").value = "0.0";
  document.getElementById("tf-pos-z").value = "0.0";
  syncUIToMesh();
  document.getElementById("status-text").textContent = "Model centered at bed origin (X: 0, Y: 0, Z: 0).";
}

function dropModelToBed() {
  document.getElementById("tf-pos-z").value = "0.0";
  syncUIToMesh();
  document.getElementById("status-text").textContent = "Model dropped to bed floor (Z=0).";
}

function resetAllTransforms() {
  resetTransformsUI();
  syncUIToMesh();
  document.getElementById("status-text").textContent = "Model transformations reset to default.";
}

function addRotation(axis, degrees) {
  const inp = document.getElementById(`tf-rot-${axis}`);
  let val = (parseFloat(inp.value || 0) + degrees) % 360;
  if (val < 0) val += 360;
  inp.value = val;
  syncUIToMesh();
  runDesignAnalysis(false);
}

function autoFitModelIntoStock() {
  if (!currentMesh) return;
  const stockW = parseFloat(document.getElementById("stock-w").value || 60);
  const stockL = parseFloat(document.getElementById("stock-l").value || 60);
  const stockT = parseFloat(document.getElementById("stock-t").value || 30);
  const mX = parseFloat(document.getElementById("model-dim-x").value || 1);
  const mY = parseFloat(document.getElementById("model-dim-y").value || 1);
  const mZ = parseFloat(document.getElementById("model-dim-z").value || 1);

  const fitX = (stockW - 10) / mX;
  const fitY = (stockL - 10) / mY;
  const fitZ = (stockT - 2) / mZ;
  const bestRatio = Math.min(fitX, fitY, fitZ);
  const pct = Math.max(10, Math.floor(bestRatio * 100));

  document.getElementById("tf-scale-x").value = pct;
  document.getElementById("tf-scale-y").value = pct;
  document.getElementById("tf-scale-z").value = pct;
  syncUIToMesh();
  document.getElementById("status-text").textContent = `Auto-fit scaled model to ${pct}% to safely fit stock boundaries.`;
}

function mirrorAxis(axis) {
  if (!currentMesh) return;
  const inp = document.getElementById(`tf-scale-${axis}`);
  const curr = parseFloat(inp.value || 100);
  inp.value = -curr;
  syncUIToMesh();
  document.getElementById("status-text").textContent = `Mirrored model along ${axis.toUpperCase()} axis.`;
}

// Smart Auto-Pilot: Automatically select tool, strategy, cutting parameters, and speeds & feeds
async function runAutoMode() {
  switchStudioTab("tab-auto-optimizer");
  applyOptimizerToolpath();
}

function applyAutoModeRecommendations(data) {
  // 1. Tool Selection
  const toolSelect = document.getElementById("tool-select");
  if (toolSelect && data.tool_key) {
    toolSelect.value = data.tool_key;
    highlightAutoElement(toolSelect);
    updateToolMesh(data.tool_diameter_mm || 3.175);
  }

  // 2. Machining Strategy (Clearing Pattern)
  const patternSelect = document.getElementById("clearing-pattern");
  if (patternSelect && data.clearing_pattern) {
    patternSelect.value = data.clearing_pattern;
    highlightAutoElement(patternSelect);
  }

  // 3. Cutting Parameters (Stepdown, Stepover, Finishing Allowance)
  const stepdownInput = document.getElementById("cut-stepdown");
  if (stepdownInput && data.stepdown_mm !== undefined) {
    stepdownInput.value = data.stepdown_mm;
    highlightAutoElement(stepdownInput);
  }
  const stepoverInput = document.getElementById("cut-stepover");
  if (stepoverInput && data.stepover_pct !== undefined) {
    stepoverInput.value = data.stepover_pct;
    highlightAutoElement(stepoverInput);
  }
  const stockToLeaveInput = document.getElementById("stock-to-leave");
  if (stockToLeaveInput && data.stock_to_leave_mm !== undefined) {
    stockToLeaveInput.value = data.stock_to_leave_mm;
    highlightAutoElement(stockToLeaveInput);
  }

  // Holding Tabs
  const tabsSelect = document.getElementById("tabs-count");
  if (tabsSelect && data.tabs_count !== undefined) {
    tabsSelect.value = data.tabs_count;
    highlightAutoElement(tabsSelect);
  }
  const tabWInput = document.getElementById("tab-width");
  if (tabWInput && data.tab_width_mm !== undefined) {
    tabWInput.value = data.tab_width_mm;
  }
  const tabHInput = document.getElementById("tab-height");
  if (tabHInput && data.tab_height_mm !== undefined) {
    tabHInput.value = data.tab_height_mm;
  }

  // 4. Speeds & Feeds (Feed XY, Feed Z, Spindle RPM)
  const feedXyInput = document.getElementById("feed-xy");
  if (feedXyInput && data.feed_xy_mm_min !== undefined) {
    feedXyInput.value = data.feed_xy_mm_min;
    highlightAutoElement(feedXyInput);
  }
  const feedZInput = document.getElementById("feed-z");
  if (feedZInput && data.feed_z_mm_min !== undefined) {
    feedZInput.value = data.feed_z_mm_min;
    highlightAutoElement(feedZInput);
  }
  const rpmInput = document.getElementById("spindle-rpm");
  if (rpmInput && data.spindle_rpm !== undefined) {
    rpmInput.value = data.spindle_rpm;
    highlightAutoElement(rpmInput);
  }

  // 5. Stock Shape & Dimensions
  if (data.recommended_stock_shape) {
    const shapeSelect = document.getElementById("stock-shape");
    if (shapeSelect) {
      shapeSelect.value = data.recommended_stock_shape;
      shapeSelect.dispatchEvent(new Event("change"));
    }

    if (data.recommended_stock_dims) {
      const d = data.recommended_stock_dims;
      if (d.width) document.getElementById("stock-w").value = d.width;
      if (d.length) document.getElementById("stock-l").value = d.length;
      if (d.thickness) document.getElementById("stock-t").value = d.thickness;
      if (d.diameter) document.getElementById("stock-d").value = d.diameter;
    }
    renderCurrentStockMesh();
  }

  // 6. Update Telemetry Card & Rationale List
  const telemCard = document.getElementById("auto-mode-telemetry");
  if (telemCard) {
    telemCard.style.display = "block";
    const pill = document.getElementById("auto-summary-pill");
    if (pill) pill.textContent = `Optimal: ${data.summary}`;

    const list = document.getElementById("auto-reasons-list");
    if (list && data.reasons) {
      list.innerHTML = "";
      data.reasons.forEach((reason) => {
        const li = document.createElement("li");
        li.textContent = reason;
        list.appendChild(li);
      });
    }
  }

  // 7. Update status bar
  document.getElementById("status-text").textContent = `Auto Mode Applied: ${data.tool_name} • ${data.clearing_pattern} pattern • ${data.stepdown_mm}mm DOC • ${data.spindle_rpm.toLocaleString()} RPM @ ${data.feed_xy_mm_min} mm/min.`;
}

function highlightAutoElement(el) {
  if (!el) return;
  el.classList.remove("auto-highlight");
  void el.offsetWidth; // Force CSS reflow to restart keyframe
  el.classList.add("auto-highlight");
  setTimeout(() => el.classList.remove("auto-highlight"), 1800);
}

// Design Analysis & Outline Generation
async function runDesignAnalysis(autoSetStock = false) {
  document.getElementById("status-text").textContent = "Analyzing design geometry and extracting boundary outlines for meshing...";

  try {
    const payload = {
      rot_x: parseFloat(document.getElementById("tf-rot-x") ? document.getElementById("tf-rot-x").value : 0),
      rot_y: parseFloat(document.getElementById("tf-rot-y") ? document.getElementById("tf-rot-y").value : 0),
      rot_z: parseFloat(document.getElementById("tf-rot-z") ? document.getElementById("tf-rot-z").value : 0),
      scale_x: parseFloat(document.getElementById("tf-scale-x") ? document.getElementById("tf-scale-x").value : 100) / 100,
      scale_y: parseFloat(document.getElementById("tf-scale-y") ? document.getElementById("tf-scale-y").value : 100) / 100,
      scale_z: parseFloat(document.getElementById("tf-scale-z") ? document.getElementById("tf-scale-z").value : 100) / 100,
      margin_xy: 5.0,
      margin_z_top: 2.0
    };

    const res = await fetch("/api/model/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) return;

    applyAnalysisResult(data, autoSetStock);
  } catch (err) {
    console.error("Design analysis failed:", err);
  }
}

// Apply Analysis Results & Outlines
function applyAnalysisResult(data, autoSetStock = false) {
  latestAnalysis = data;

  // Update UI Stats
  document.getElementById("analysis-footprint").textContent = `${data.footprint_area_mm2.toFixed(0)} mm²`;
  document.getElementById("analysis-volume").textContent = `${data.volume_cm3.toFixed(1)} cm³`;
  document.getElementById("analysis-cyl-dia").textContent = `Ø${data.min_cylinder_diameter_mm.toFixed(1)} mm`;
  const holeTag = (data.detected_holes && data.detected_holes.length > 0) ? ` • ${data.detected_holes.length} hole(s) (Ø${data.min_hole_diameter_mm}mm)` : "";
  document.getElementById("analysis-feature-count").textContent = `${data.feature_edges.length} edges${holeTag}`;

  // Auto-adapt stock shape & dimensions if autoSetStock is requested
  if (autoSetStock && data.detected_shape) {
    const shapeSelect = document.getElementById("stock-shape");
    shapeSelect.value = data.detected_shape;
    shapeSelect.dispatchEvent(new Event("change"));

    if (data.detected_shape === "round_plate" || data.detected_shape === "cylinder") {
      document.getElementById("stock-d").value = data.recommended_cylinder.diameter;
      document.getElementById("stock-t").value = data.recommended_cylinder.thickness;
    } else if (data.detected_shape === "plate") {
      document.getElementById("stock-w").value = data.recommended_plate.width;
      document.getElementById("stock-l").value = data.recommended_plate.length;
      document.getElementById("stock-t").value = data.recommended_plate.thickness;
    } else {
      document.getElementById("stock-w").value = data.recommended_box.width;
      document.getElementById("stock-l").value = data.recommended_box.length;
      document.getElementById("stock-t").value = data.recommended_box.thickness;
    }
  }

  renderCurrentStockMesh();
  renderAnalysisOutlines(data);

  const desc = data.shape_description || "Design";
  document.getElementById("status-text").textContent = `Analysis: ${desc} detected (${data.footprint_area_mm2.toFixed(0)} mm², Ø${data.min_cylinder_diameter_mm.toFixed(1)}mm). Material silhouette fitted.`;
}

// Render 2D Footprint Silhouette & 3D Feature Edge Curves
function renderAnalysisOutlines(data) {
  while (silhouetteOutlineGroup.children.length > 0) {
    silhouetteOutlineGroup.remove(silhouetteOutlineGroup.children[0]);
  }
  while (featureEdgesGroup.children.length > 0) {
    featureEdgesGroup.remove(featureEdgesGroup.children[0]);
  }

  // 1. 2D Silhouette Footprint on Bed Floor
  if (data.silhouette_exterior && data.silhouette_exterior.length > 2) {
    const extPts = data.silhouette_exterior.map((p) => new THREE.Vector3(p[0], p[1], 0.2));
    const extGeo = new THREE.BufferGeometry().setFromPoints(extPts);
    const extMat = new THREE.LineBasicMaterial({ color: 0x00ffff, linewidth: 2 });
    const extLine = new THREE.LineLoop(extGeo, extMat);
    silhouetteOutlineGroup.add(extLine);
  }

  if (data.silhouette_interiors) {
    data.silhouette_interiors.forEach((ring) => {
      const intPts = ring.map((p) => new THREE.Vector3(p[0], p[1], 0.2));
      const intGeo = new THREE.BufferGeometry().setFromPoints(intPts);
      const intMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, linewidth: 1.5 });
      const intLine = new THREE.LineLoop(intGeo, intMat);
      silhouetteOutlineGroup.add(intLine);
    });
  }

  if (currentMesh) {
    silhouetteOutlineGroup.position.set(currentMesh.position.x, currentMesh.position.y, 0);
  }

  // 2. 3D Feature Edge Curves (creases, steps, sharp profile curves)
  if (data.feature_edges && data.feature_edges.length > 0) {
    const edgePts = [];
    data.feature_edges.forEach((segment) => {
      edgePts.push(new THREE.Vector3(segment[0][0], segment[0][1], segment[0][2]));
      edgePts.push(new THREE.Vector3(segment[1][0], segment[1][1], segment[1][2]));
    });

    const edgesGeo = new THREE.BufferGeometry().setFromPoints(edgePts);
    const edgesMat = new THREE.LineBasicMaterial({ color: 0xfbbf24, linewidth: 2 }); // Rich gold
    const edgesLines = new THREE.LineSegments(edgesGeo, edgesMat);
    featureEdgesGroup.add(edgesLines);
  }

  if (currentMesh) {
    featureEdgesGroup.position.copy(currentMesh.position);
    featureEdgesGroup.rotation.copy(currentMesh.rotation);
    featureEdgesGroup.scale.copy(currentMesh.scale);
  }

  silhouetteOutlineGroup.visible = document.getElementById("chk-show-silhouette").checked;
  featureEdgesGroup.visible = document.getElementById("chk-show-feature-edges").checked;
}

// Auto-Fit Raw Stock Dimensions around current design
function autoFitStockToDesign() {
  if (!latestAnalysis) {
    runDesignAnalysis(true);
    return;
  }

  const shape = document.getElementById("stock-shape").value;
  if (shape === "cylinder" || shape === "round_plate") {
    document.getElementById("stock-d").value = latestAnalysis.recommended_cylinder.diameter;
    document.getElementById("stock-t").value = latestAnalysis.recommended_cylinder.thickness;
  } else if (shape === "plate") {
    document.getElementById("stock-w").value = latestAnalysis.recommended_plate.width;
    document.getElementById("stock-l").value = latestAnalysis.recommended_plate.length;
    document.getElementById("stock-t").value = latestAnalysis.recommended_plate.thickness;
  } else {
    document.getElementById("stock-w").value = latestAnalysis.recommended_box.width;
    document.getElementById("stock-l").value = latestAnalysis.recommended_box.length;
    document.getElementById("stock-t").value = latestAnalysis.recommended_box.thickness;
  }

  renderCurrentStockMesh();
  document.getElementById("status-text").textContent = `Stock auto-fitted to design boundaries with machining margins.`;
}

// Setup Stock and Analysis Event Listeners
function setupStockAndAnalysisControls() {
  const shapeSelect = document.getElementById("stock-shape");
  const boxDims = document.getElementById("stock-box-dims");
  const cylDims = document.getElementById("stock-cylinder-dims");
  const conformingDims = document.getElementById("stock-conforming-dims");

  shapeSelect.addEventListener("change", () => {
    const val = shapeSelect.value;
    boxDims.style.display = (val === "box" || val === "plate") ? "block" : "none";
    cylDims.style.display = (val === "cylinder" || val === "round_plate") ? "block" : "none";
    conformingDims.style.display = (val === "conforming") ? "block" : "none";

    // Auto-update dimensions when shape changes if analysis exists
    if (latestAnalysis) {
      if ((val === "cylinder" || val === "round_plate") && latestAnalysis.recommended_cylinder) {
        document.getElementById("stock-d").value = latestAnalysis.recommended_cylinder.diameter;
        document.getElementById("stock-t").value = latestAnalysis.recommended_cylinder.thickness;
      } else if (val === "plate" && latestAnalysis.recommended_plate) {
        document.getElementById("stock-w").value = latestAnalysis.recommended_plate.width;
        document.getElementById("stock-l").value = latestAnalysis.recommended_plate.length;
        document.getElementById("stock-t").value = latestAnalysis.recommended_plate.thickness;
      } else if (val === "box" && latestAnalysis.recommended_box) {
        document.getElementById("stock-w").value = latestAnalysis.recommended_box.width;
        document.getElementById("stock-l").value = latestAnalysis.recommended_box.length;
        document.getElementById("stock-t").value = latestAnalysis.recommended_box.thickness;
      }
    }

    renderCurrentStockMesh();
  });

  // Re-render stock on dimension input changes
  ["stock-w", "stock-l", "stock-t", "stock-d", "stock-conforming-margin"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", renderCurrentStockMesh);
  });

  // Auto-fit & Center stock buttons
  document.getElementById("btn-fit-stock-to-design").addEventListener("click", autoFitStockToDesign);
  document.getElementById("btn-center-stock-model").addEventListener("click", () => {
    if (currentMesh) {
      syncStockToModelPosition();
      document.getElementById("status-text").textContent = "Stock centered directly onto 3D model.";
    }
  });

  // Analysis run & preset buttons
  document.getElementById("btn-run-analysis").addEventListener("click", () => runDesignAnalysis(false));
  const btnAnalyzeHeader = document.getElementById("btn-analyze-model");
  if (btnAnalyzeHeader) btnAnalyzeHeader.addEventListener("click", () => runDesignAnalysis(false));

  document.getElementById("btn-apply-rec-box").addEventListener("click", () => {
    shapeSelect.value = "box";
    shapeSelect.dispatchEvent(new Event("change"));
  });

  document.getElementById("btn-apply-rec-cylinder").addEventListener("click", () => {
    shapeSelect.value = "round_plate";
    shapeSelect.dispatchEvent(new Event("change"));
  });

  document.getElementById("btn-apply-rec-plate").addEventListener("click", () => {
    shapeSelect.value = "plate";
    shapeSelect.dispatchEvent(new Event("change"));
  });

  // Outline visibility toggles
  document.getElementById("chk-show-silhouette").addEventListener("change", (e) => {
    silhouetteOutlineGroup.visible = e.target.checked;
  });
  document.getElementById("chk-show-feature-edges").addEventListener("change", (e) => {
    featureEdgesGroup.visible = e.target.checked;
  });
}

// Setup Creality Toolbar, Drawer, and Transform Event Listeners
function setupCrealityControls() {
  document.getElementById("tool-select-btn").addEventListener("click", () => setActiveTool("select"));
  document.getElementById("tool-translate-btn").addEventListener("click", () => setActiveTool("translate"));
  document.getElementById("tool-rotate-btn").addEventListener("click", () => setActiveTool("rotate"));
  document.getElementById("tool-scale-btn").addEventListener("click", () => setActiveTool("scale"));
  document.getElementById("tool-mirror-btn").addEventListener("click", () => setActiveTool("mirror"));
  document.getElementById("tool-layflat-btn").addEventListener("click", layFlatModel);
  document.getElementById("tool-center-btn").addEventListener("click", centerModelInStock);
  document.getElementById("tool-reset-btn").addEventListener("click", resetAllTransforms);

  document.getElementById("drawer-close").addEventListener("click", () => setActiveTool("select"));

  document.getElementById("btn-drawer-center").addEventListener("click", centerModelInStock);
  document.getElementById("btn-drawer-drop").addEventListener("click", dropModelToBed);

  document.getElementById("btn-rot-x-90").addEventListener("click", () => addRotation("x", 90));
  document.getElementById("btn-rot-y-90").addEventListener("click", () => addRotation("y", 90));
  document.getElementById("btn-rot-z-90").addEventListener("click", () => addRotation("z", 90));
  document.getElementById("btn-drawer-layflat").addEventListener("click", layFlatModel);
  document.getElementById("btn-drawer-rot-reset").addEventListener("click", () => {
    document.getElementById("tf-rot-x").value = "0";
    document.getElementById("tf-rot-y").value = "0";
    document.getElementById("tf-rot-z").value = "0";
    syncUIToMesh();
    runDesignAnalysis(false);
  });

  const uniformChk = document.getElementById("tf-scale-uniform");
  ["x", "y", "z"].forEach((axis) => {
    const input = document.getElementById(`tf-scale-${axis}`);
    input.addEventListener("input", (e) => {
      if (uniformChk.checked) {
        ["x", "y", "z"].forEach((otherAxis) => {
          if (otherAxis !== axis) {
            document.getElementById(`tf-scale-${otherAxis}`).value = e.target.value;
          }
        });
      }
      syncUIToMesh();
    });
    input.addEventListener("change", () => runDesignAnalysis(false));
  });

  document.getElementById("btn-drawer-fit").addEventListener("click", autoFitModelIntoStock);
  document.getElementById("btn-drawer-scale-reset").addEventListener("click", () => {
    document.getElementById("tf-scale-x").value = "100";
    document.getElementById("tf-scale-y").value = "100";
    document.getElementById("tf-scale-z").value = "100";
    syncUIToMesh();
    runDesignAnalysis(false);
  });

  document.getElementById("btn-mirror-x").addEventListener("click", () => mirrorAxis("x"));
  document.getElementById("btn-mirror-y").addEventListener("click", () => mirrorAxis("y"));
  document.getElementById("btn-mirror-z").addEventListener("click", () => mirrorAxis("z"));

  ["tf-pos-x", "tf-pos-y", "tf-pos-z", "tf-rot-x", "tf-rot-y", "tf-rot-z"].forEach((id) => {
    document.getElementById(id).addEventListener("input", syncUIToMesh);
  });

  const tabsCountSelect = document.getElementById("tabs-count");
  const tabsSpecsRow = document.getElementById("tabs-specs-row");
  tabsCountSelect.addEventListener("change", () => {
    const hasTabs = parseInt(tabsCountSelect.value) > 0;
    tabsSpecsRow.style.opacity = hasTabs ? "1" : "0.4";
    tabsSpecsRow.style.pointerEvents = hasTabs ? "auto" : "none";
  });
  tabsSpecsRow.style.opacity = "0.4";
  tabsSpecsRow.style.pointerEvents = "none";

  window.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.key === "Escape") setActiveTool("select");
    else if (e.key === "w" || e.key === "W") setActiveTool("translate");
    else if (e.key === "e" || e.key === "E") setActiveTool("rotate");
    else if (e.key === "r" || e.key === "R") setActiveTool("scale");
    else if (e.key === "m" || e.key === "M") setActiveTool("mirror");
  });
}

// Load Sample 2 (Stepped Pyramid 3D model)
async function loadSamplePyramid() {
  currentModelType = "sample2";
  try {
    const res = await fetch("/api/model/sample_pyramid");
    const data = await res.json();
    if (data.success) {
      document.getElementById("stock-w").value = 70;
      document.getElementById("stock-l").value = 70;
      document.getElementById("stock-t").value = 32;
      document.getElementById("stock-d").value = 90;
      display3DMesh(data.vertices, data.faces, data.metadata, data.analysis);
    }
  } catch (err) {
    console.error("Failed to load sample pyramid:", err);
  }
}

// Upload & Insert Arbitrary STL / OBJ
async function uploadStlFile(file) {
  currentModelType = "custom_stl";
  const formData = new FormData();
  formData.append("file", file);

  document.getElementById("status-text").textContent = `Parsing 3D Model: ${file.name}...`;

  try {
    const res = await fetch("/api/model/upload_stl", {
      method: "POST",
      body: formData
    });
    const data = await res.json();

    if (data.error) {
      alert("Error loading 3D model: " + data.error);
      return;
    }

    display3DMesh(data.vertices, data.faces, data.metadata, data.analysis);
  } catch (err) {
    console.error("Upload error:", err);
    alert("Upload failed. See console.");
  }
}

function setupDragAndDrop(container) {
  container.addEventListener("dragover", (e) => {
    e.preventDefault();
    container.style.border = "2px dashed #007acc";
  });
  container.addEventListener("dragleave", (e) => {
    e.preventDefault();
    container.style.border = "none";
  });
  container.addEventListener("drop", (e) => {
    e.preventDefault();
    container.style.border = "none";
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadStlFile(e.dataTransfer.files[0]);
    }
  });
}

// Trigger Toolhead LiDAR Auto-Scan
async function autoScanBedLidar() {
  const badge = document.getElementById("lidar-status-badge");
  if (badge) {
    badge.textContent = "Scanning Bed...";
    badge.style.background = "#7e22ce";
  }

  setStatus("Toolhead LiDAR sweeping machine bed to locate stock coordinates & surface flatness...", "scan");

  const stockW = parseFloat(document.getElementById("stock-w").value || 70);
  const stockL = parseFloat(document.getElementById("stock-l").value || 70);
  const stockT = parseFloat(document.getElementById("stock-t").value || 30);
  let stockX = -stockW / 2;
  let stockY = -stockL / 2;
  if (currentMesh) {
    stockX = currentMesh.position.x - stockW / 2;
    stockY = currentMesh.position.y - stockL / 2;
  }

  try {
    const res = await fetch("/api/lidar/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bed_w: currentMachine ? currentMachine.bed_width_x : 300,
        bed_l: currentMachine ? currentMachine.bed_length_y : 180,
        stock_x: stockX,
        stock_y: stockY,
        stock_w: stockW,
        stock_l: stockL,
        stock_t: stockT
      })
    });
    const data = await res.json();

    if (data.detected) {
      document.getElementById("stock-pos").value = `X: ${data.x_min.toFixed(1)}, Y: ${data.y_min.toFixed(1)}`;
      document.getElementById("surface-warp-val").textContent = `${data.max_deviation.toFixed(2)} mm (Auto-Leveled)`;

      renderCurrentStockMesh();
      if (badge) {
        badge.textContent = "Stock Located (Auto)";
        badge.style.background = "#065f46";
      }
      setStatus(`LiDAR Detection: Located ${data.width.toFixed(1)} x ${data.length.toFixed(1)} x ${data.thickness.toFixed(1)} mm stock. Auto-leveling active.`, "ready");
    }
  } catch (err) {
    console.error("Scan error:", err);
    setStatus("LiDAR surface scan failed.", "error");
  }
}

// Slicing Core with Stock Geometry and Position Locking
async function sliceCurrentModel() {
  const sliceBtn = document.getElementById("btn-slice");
  sliceBtn.disabled = true;
  sliceBtn.textContent = "Slicing...";
  setStatus("Calculating Subtractive Slices: Stock(Z) - Part(Z)...", "busy");

  try {
    const payload = {
      model_type: currentModelType,
      machine_id: currentMachine ? currentMachine.id : "genmitsu_3018",
      stock_shape: document.getElementById("stock-shape").value,
      stock_w: parseFloat(document.getElementById("stock-w").value),
      stock_l: parseFloat(document.getElementById("stock-l").value),
      stock_t: parseFloat(document.getElementById("stock-t").value),
      stock_d: parseFloat(document.getElementById("stock-d") ? document.getElementById("stock-d").value : 90),
      stock_lock_to_model: document.getElementById("stock-lock-to-model").checked,
      margin_xy: parseFloat(document.getElementById("stock-conforming-margin") ? document.getElementById("stock-conforming-margin").value : 5.0),

      stepdown: parseFloat(document.getElementById("cut-stepdown").value),
      stepover: parseFloat(document.getElementById("cut-stepover").value) / 100.0,
      feed_xy: parseFloat(document.getElementById("feed-xy").value),
      feed_z: parseFloat(document.getElementById("feed-z").value),
      tool_d: document.getElementById("tool-select").value === "flat_1_4" ? 6.35 : 3.175,

      // Creality Transforms
      pos_x: parseFloat(document.getElementById("tf-pos-x").value || 0),
      pos_y: parseFloat(document.getElementById("tf-pos-y").value || 0),
      pos_z: parseFloat(document.getElementById("tf-pos-z").value || 0),
      rot_x: parseFloat(document.getElementById("tf-rot-x").value || 0),
      rot_y: parseFloat(document.getElementById("tf-rot-y").value || 0),
      rot_z: parseFloat(document.getElementById("tf-rot-z").value || 0),
      scale_x: parseFloat(document.getElementById("tf-scale-x").value || 100) / 100.0,
      scale_y: parseFloat(document.getElementById("tf-scale-y").value || 100) / 100.0,
      scale_z: parseFloat(document.getElementById("tf-scale-z").value || 100) / 100.0,

      // Infill Pattern & Tabs
      clearing_pattern: document.getElementById("clearing-pattern").value,
      stock_to_leave: parseFloat(document.getElementById("stock-to-leave").value || 0),
      tabs_count: parseInt(document.getElementById("tabs-count").value || 0),
      tab_width: parseFloat(document.getElementById("tab-width").value || 5.0),
      tab_height: parseFloat(document.getElementById("tab-height").value || 2.0)
    };

    const res = await fetch("/api/slice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    currentSlicingData = data;

    // Update stats
    document.getElementById("stat-layers").textContent = data.layer_count;
    document.getElementById("stat-cut-dist").textContent = `${data.cut_distance_mm.toFixed(0)} mm`;
    document.getElementById("stat-rapid-dist").textContent = `${data.rapid_distance_mm.toFixed(0)} mm`;
    document.getElementById("stat-est-time").textContent = `${data.estimated_minutes.toFixed(1)} min`;
    const optTime = document.getElementById("opt-status-time");
    if (optTime) {
      optTime.textContent = `${data.estimated_minutes.toFixed(1)} min`;
    }

    // Configure vertical layer slider
    const slider = document.getElementById("layer-slider");
    slider.min = "0";
    slider.max = String(data.layer_count);
    slider.value = String(data.layer_count);
    document.getElementById("current-layer-label").textContent = `1 - ${data.layer_count}`;

    // Render toolpaths
    renderToolpaths(data.layers);

    document.getElementById("btn-view-gcode").disabled = false;
    document.getElementById("btn-export-gcode").disabled = false;
    setStatus(`Slicing complete: ${data.layer_count} layers computed. Scrub layer slider or click ▶ to simulate tool.`, "ready");
  } catch (err) {
    console.error("Slicing failed:", err);
    setStatus("Slicing failed. Check console.", "error");
  } finally {
    sliceBtn.disabled = false;
    sliceBtn.innerHTML = '<span class="slice-icon"><svg class="svg-icon" viewBox="0 0 24 24"><path d="M4 14l8-8 8 8-8 8-8-8z"/><line x1="2" y1="12" x2="22" y2="12"/></svg></span> Slice Stock';
  }
}

// Render Toolpaths into 3D Viewport
function renderToolpaths(layers, maxVisibleLayerIndex = 9999) {
  while (toolpathLinesGroup.children.length > 0) {
    toolpathLinesGroup.remove(toolpathLinesGroup.children[0]);
  }

  const cutMat = new THREE.LineBasicMaterial({ color: 0x00ffff, linewidth: 2 });
  const rapidMat = new THREE.LineDashedMaterial({
    color: 0xf87171,
    linewidth: 1,
    dashSize: 3,
    gapSize: 2
  });

  layers.forEach((lyr, lIdx) => {
    if (lIdx > maxVisibleLayerIndex) return;

    lyr.segments.forEach((seg) => {
      const cutPoints = [];
      const rapidPoints = [];

      for (let i = 0; i < seg.points.length - 1; i++) {
        const p1 = seg.points[i];
        const p2 = seg.points[i + 1];

        if (p2.move_type === "cut" || p2.move_type === "plunge") {
          cutPoints.push(new THREE.Vector3(p1.x, p1.y, p1.z));
          cutPoints.push(new THREE.Vector3(p2.x, p2.y, p2.z));
        } else {
          rapidPoints.push(new THREE.Vector3(p1.x, p1.y, p1.z));
          rapidPoints.push(new THREE.Vector3(p2.x, p2.y, p2.z));
        }
      }

      if (cutPoints.length > 0) {
        const geo = new THREE.BufferGeometry().setFromPoints(cutPoints);
        const line = new THREE.LineSegments(geo, cutMat);
        toolpathLinesGroup.add(line);
      }

      if (rapidPoints.length > 0) {
        const geo = new THREE.BufferGeometry().setFromPoints(rapidPoints);
        const line = new THREE.LineSegments(geo, rapidMat);
        line.computeLineDistances();
        toolpathLinesGroup.add(line);
      }
    });
  });
}

// Vertical Layer Slider Scrubbing
function onLayerSliderInput() {
  if (!currentSlicingData) return;
  const val = parseInt(document.getElementById("layer-slider").value);
  const total = currentSlicingData.layer_count;

  if (val === 0 || val === total) {
    document.getElementById("current-layer-label").textContent = `1 - ${total}`;
    renderToolpaths(currentSlicingData.layers, total);
  } else {
    document.getElementById("current-layer-label").textContent = `Pass ${val}`;
    const nominalZ = currentSlicingData.layers[val - 1]?.nominal_z || 0;
    document.getElementById("layer-z-val").textContent = `Z: ${nominalZ.toFixed(2)}mm`;
    renderToolpaths(currentSlicingData.layers, val - 1);
  }
}

// 3D Cutting Simulation Loop
function toggleSimulation() {
  if (!currentSlicingData || currentSlicingData.layers.length === 0) return;

  const simBtn = document.getElementById("btn-play-sim");

  if (isSimulating) {
    clearInterval(simInterval);
    isSimulating = false;
    simBtn.innerHTML = '<svg viewBox="0 0 24 24" style="width: 14px; height: 14px; fill: #ffffff; stroke: none; margin-left: 2px;"><polygon points="6 4 20 12 6 20 6 4"/></svg>';
    toolMeshGroup.visible = false;
    document.getElementById("status-text").textContent = "Simulation paused.";
    return;
  }

  const layerVal = parseInt(document.getElementById("layer-slider").value);
  const layersToSim = (layerVal === 0 || layerVal === currentSlicingData.layer_count)
    ? currentSlicingData.layers
    : [currentSlicingData.layers[layerVal - 1]];

  simPoints = [];
  layersToSim.forEach((lyr) => {
    lyr.segments.forEach((seg) => {
      seg.points.forEach((pt) => simPoints.push(pt));
    });
  });

  if (simPoints.length === 0) return;

  isSimulating = true;
  simIndex = 0;
  simBtn.innerHTML = '<svg viewBox="0 0 24 24" style="width: 14px; height: 14px; fill: #ffffff; stroke: none;"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
  toolMeshGroup.visible = true;

  const speedMult = parseFloat(document.getElementById("sim-speed-select").value) || 5;
  const intervalMs = Math.max(10, Math.floor(100 / speedMult));

  simInterval = setInterval(() => {
    if (simIndex >= simPoints.length) {
      clearInterval(simInterval);
      isSimulating = false;
      simBtn.innerHTML = '<svg viewBox="0 0 24 24" style="width: 14px; height: 14px; fill: #ffffff; stroke: none; margin-left: 2px;"><polygon points="6 4 20 12 6 20 6 4"/></svg>';
      document.getElementById("status-text").textContent = "Toolpath simulation finished.";
      return;
    }

    const pt = simPoints[simIndex];
    toolMeshGroup.position.set(pt.x, pt.y, pt.z);
    document.getElementById("status-text").textContent = `Simulating: [${pt.move_type.toUpperCase()}] X:${pt.x.toFixed(2)} Y:${pt.y.toFixed(2)} Z:${pt.z.toFixed(2)} mm`;
    document.getElementById("layer-z-val").textContent = `Z: ${pt.z.toFixed(2)}mm`;

    simIndex += Math.ceil(speedMult / 5);
  }, intervalMs);
}

// G-Code View, Machine Verification & Export
async function viewGCode() {
  try {
    const res = await fetch("/api/gcode/text");
    const data = await res.json();
    document.getElementById("gcode-display-text").textContent = data.gcode;
    document.getElementById("gcode-lines-count").textContent = `${data.lines} lines`;

    // Fetch Machine Verification Diagnostics
    try {
      const vRes = await fetch("/api/gcode/verify");
      const vData = await vRes.json();
      if (vData.success && vData.report) {
        const rep = vData.report;
        const badge = document.getElementById("verify-status-badge");
        badge.className = `verify-badge ${rep.status.toLowerCase()}`;
        if (rep.status === "PASS") {
          badge.textContent = `VERIFIED: READY TO RUN (${rep.cutting_blocks} CUT BLOCKS)`;
        } else if (rep.status === "WARNING") {
          badge.textContent = `READY WITH ${rep.warnings.length} NOTICE(S)`;
        } else {
          badge.textContent = `ERROR: ${rep.errors[0] || "COLLISION DETECTED"}`;
        }

        const b = rep.bounds;
        document.getElementById("verify-bounds").textContent = `X[${b.x_min}, ${b.x_max}] Y[${b.y_min}, ${b.y_max}] Z[${b.z_min}, ${b.z_max}]`;
        document.getElementById("verify-feed").textContent = `${rep.max_feedrate_xy} mm/min`;
        document.getElementById("verify-spindle").textContent = `${rep.spindle_rpm} RPM`;
      }
    } catch (vErr) {
      console.warn("GCode verification call skipped:", vErr);
    }

    document.getElementById("gcode-modal").style.display = "flex";
  } catch (err) {
    console.error("GCode view error:", err);
  }
}

function copyGCodeToClipboard() {
  const text = document.getElementById("gcode-display-text").textContent;
  const btn = document.getElementById("btn-copy-gcode");
  navigator.clipboard.writeText(text).then(() => {
    if (btn) {
      const origText = btn.textContent;
      btn.textContent = "Copied!";
      btn.style.color = "#34d399";
      btn.style.borderColor = "#059669";
      setTimeout(() => {
        btn.textContent = origText;
        btn.style.color = "";
        btn.style.borderColor = "";
      }, 2000);
    }
  });
}

function closeGCodeModal() {
  document.getElementById("gcode-modal").style.display = "none";
}

function exportGCodeFile() {
  if (!currentSlicingData) return;
  window.location.href = "/api/gcode/download";
}

// Camera Preset Views (Dynamically framed to machine bed dimensions)
function setCameraView(view) {
  const stockT = parseFloat(document.getElementById("stock-t").value || 30);
  let cx = 0;
  let cy = 0;
  const cz = stockT / 2;

  if (currentMesh) {
    cx = currentMesh.position.x;
    cy = currentMesh.position.y;
  }

  controls.target.set(cx, cy, cz);

  const span = currentMachine ? Math.max(currentMachine.bed_width_x, currentMachine.bed_length_y) : 300;
  const dist = Math.max(160, span * 0.7);

  if (view === "top") {
    camera.up.set(0, 1, 0);
    camera.position.set(cx, cy, cz + dist * 1.3);
  } else if (view === "front") {
    camera.up.set(0, 0, 1);
    camera.position.set(cx, cy - dist * 1.3, cz + dist * 0.25);
  } else if (view === "iso") {
    camera.up.set(0, 0, 1);
    camera.position.set(cx + dist * 0.7, cy - dist * 0.9, cz + dist * 0.7);
  }

  camera.lookAt(controls.target);
  controls.update();

  // Update active pill state on camera view buttons
  document.querySelectorAll(".views-control-capsule .btn-view-pill").forEach(btn => {
    btn.classList.remove("active");
  });
  const activeBtn = document.getElementById(`btn-view-${view}`);
  if (activeBtn) {
    activeBtn.classList.add("active");
  }
}

// Render loop
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// Bind UI Events
window.addEventListener("DOMContentLoaded", () => {
  initViewport();

  // Model & LiDAR actions
  const fileInput = document.getElementById("stl-file-input");
  document.getElementById("btn-upload-stl").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadStlFile(e.target.files[0]);
    }
  });

  document.getElementById("btn-scan-lidar").addEventListener("click", autoScanBedLidar);
  document.getElementById("btn-load-sample2").addEventListener("click", loadSamplePyramid);
  document.getElementById("btn-slice").addEventListener("click", sliceCurrentModel);

  // G-Code actions
  document.getElementById("btn-view-gcode").addEventListener("click", viewGCode);
  document.getElementById("btn-copy-gcode").addEventListener("click", copyGCodeToClipboard);
  document.getElementById("btn-close-gcode").addEventListener("click", closeGCodeModal);
  document.getElementById("btn-export-gcode").addEventListener("click", exportGCodeFile);

  // Layer scrubber & Simulation
  document.getElementById("layer-slider").addEventListener("input", onLayerSliderInput);
  document.getElementById("btn-play-sim").addEventListener("click", toggleSimulation);

  // Camera presets
  document.getElementById("btn-view-top").addEventListener("click", () => setCameraView("top"));
  document.getElementById("btn-view-front").addEventListener("click", () => setCameraView("front"));
  document.getElementById("btn-view-iso").addEventListener("click", () => setCameraView("iso"));
  document.getElementById("btn-reset-cam").addEventListener("click", () => setCameraView("iso"));

  // Auto Mode actions
  const btnAutoMode = document.getElementById("btn-auto-mode");
  if (btnAutoMode) {
    btnAutoMode.addEventListener("click", () => switchStudioTab("tab-auto-optimizer"));
  }
  const btnTriggerAuto = document.getElementById("btn-trigger-auto-mode");
  if (btnTriggerAuto) btnTriggerAuto.addEventListener("click", runAutoMode);
  const btnGotoOpt = document.getElementById("btn-goto-optimizer-tab");
  if (btnGotoOpt) {
    btnGotoOpt.addEventListener("click", () => switchStudioTab("tab-auto-optimizer"));
  }

  // Tool selection updates 3D tool model
  const toolSelect = document.getElementById("tool-select");
  if (toolSelect) {
    toolSelect.addEventListener("change", (e) => {
      const toolMap = {
        flat_1_8: 3.175,
        flat_1_4: 6.35,
        flat_6mm: 6.0,
        v_bit_60: 6.35
      };
      updateToolMesh(toolMap[e.target.value] || 3.175);
    });
  }

  // Collapsible Accordion Panels
  setupCollapsiblePanels();

  // Setup Auto-Machining Optimizer Studio
  setupAutoOptimizerStudio();

  // Setup Resizable Sidebar & Collapse/Expand Splitter
  setupSidebarResizer();

  // Setup Apple Liquid Glass Segmented Controls & Theme Switcher
  setupStudioGlassSlider();
  setupThemeSwitching();
});

// Setup accordion collapsible panels for polished, decluttered sidebar
function setupCollapsiblePanels() {
  document.querySelectorAll(".panel-title").forEach((title) => {
    title.addEventListener("click", () => {
      const panel = title.closest(".panel");
      if (panel) {
        panel.classList.toggle("collapsed");
      }
    });
  });
}

// Sidebar Resizer & 1-Click Collapse/Expand Controller (Creality / OrcaSlicer Style)
function setupSidebarResizer() {
  const resizer = document.getElementById("sidebar-resizer");
  const toggleBtn = document.getElementById("btn-toggle-sidebar");
  const arrow = document.getElementById("resizer-arrow");
  const sidebars = [
    document.getElementById("sidebar-manual-cam"),
    document.getElementById("sidebar-auto-optimizer")
  ].filter(Boolean);

  if (!resizer || sidebars.length === 0) return;

  const MIN_WIDTH = 260;
  const MAX_WIDTH = 680;
  const DEFAULT_WIDTH = 340;

  let currentWidth = parseInt(localStorage.getItem("cnc_slicer_sidebar_width"), 10) || DEFAULT_WIDTH;
  let isCollapsed = localStorage.getItem("cnc_slicer_sidebar_collapsed") === "true";
  let isDragging = false;
  let startX = 0;
  let startWidth = 0;

  function applySidebarWidth(w, save = true) {
    currentWidth = Math.max(MIN_WIDTH, Math.min(w, MAX_WIDTH));
    document.documentElement.style.setProperty("--sidebar-width", `${currentWidth}px`);
    sidebars.forEach((sb) => {
      sb.style.width = `${currentWidth}px`;
    });
    if (save) {
      localStorage.setItem("cnc_slicer_sidebar_width", currentWidth);
    }
  }

  function setCollapsedState(collapsed, save = true) {
    isCollapsed = collapsed;
    sidebars.forEach((sb) => {
      sb.classList.toggle("sidebar-collapsed", isCollapsed);
    });
    if (resizer) {
      resizer.classList.toggle("is-collapsed", isCollapsed);
    }
    if (arrow) {
      // Right-side inspector: when collapsed, arrow points left (◀) to expand.
      // When expanded, arrow points right (▶) to collapse into right margin.
      arrow.textContent = isCollapsed ? "◀" : "▶";
    }
    if (toggleBtn) {
      toggleBtn.title = isCollapsed
        ? "Expand Settings Inspector (Ctrl+B)"
        : "Collapse Settings Inspector (Ctrl+B)";
    }
    if (save) {
      localStorage.setItem("cnc_slicer_sidebar_collapsed", isCollapsed ? "true" : "false");
    }
    onWindowResize();
  }

  // Initialize width and collapsed state from saved settings
  applySidebarWidth(currentWidth, false);
  if (isCollapsed) {
    setCollapsedState(true, false);
  }

  // 1-Click Collapse / Expand Toggle
  if (toggleBtn) {
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      setCollapsedState(!isCollapsed);
    });
  }

  // Double-click splitter to reset to default width
  resizer.addEventListener("dblclick", () => {
    if (isCollapsed) {
      setCollapsedState(false);
    }
    applySidebarWidth(DEFAULT_WIDTH);
    onWindowResize();
  });

  // Mouse Drag Resizing Logic (Right-docked inspector: moving mouse LEFT increases width)
  resizer.addEventListener("mousedown", (e) => {
    if (e.target.closest("#btn-toggle-sidebar")) return;

    isDragging = true;
    startX = e.clientX;
    const activeSidebar = sidebars.find((sb) => sb.style.display !== "none") || sidebars[0];
    startWidth = isCollapsed ? 0 : (activeSidebar.offsetWidth || currentWidth);

    document.body.classList.add("is-resizing");
    resizer.classList.add("active");

    const onMouseMove = (moveEvt) => {
      if (!isDragging) return;
      const deltaX = moveEvt.clientX - startX;
      // Dragging left (negative deltaX) increases right sidebar width:
      const targetWidth = startWidth - deltaX;

      if (targetWidth < 140) {
        if (!isCollapsed) setCollapsedState(true);
      } else {
        if (isCollapsed) setCollapsedState(false);
        applySidebarWidth(targetWidth);
        onWindowResize();
      }
    };

    const onMouseUp = () => {
      if (isDragging) {
        isDragging = false;
        document.body.classList.remove("is-resizing");
        resizer.classList.remove("active");
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        onWindowResize();
      }
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  });

  // Keyboard Shortcut: Ctrl+B to toggle sidebar collapse/expand
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B")) {
      e.preventDefault();
      setCollapsedState(!isCollapsed);
    }
  });
}

// ==========================================================================
// Auto-Machining Multi-Objective Optimizer Studio Suite
// ==========================================================================
let optState = {
  profile: "balanced",
  material: "wood_mdf",
  finish: "standard"
};

let latestOptimizerData = null;

function setupAutoOptimizerStudio() {
  // 1. Studio Tab Switching
  const tabManual = document.getElementById("tab-btn-manual");
  const tabAuto = document.getElementById("tab-btn-auto-optimizer");
  if (tabManual) tabManual.addEventListener("click", () => switchStudioTab("tab-manual"));
  if (tabAuto) tabAuto.addEventListener("click", () => switchStudioTab("tab-auto-optimizer"));

  // 2. Optimization Profile Cards
  document.querySelectorAll(".opt-profile-card").forEach((card) => {
    card.addEventListener("click", () => {
      document.querySelectorAll(".opt-profile-card").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      optState.profile = card.dataset.profile || "balanced";

      const warnBanner = document.getElementById("opt-fastest-warning");
      if (warnBanner) {
        warnBanner.style.display = optState.profile === "fastest" ? "flex" : "none";
      }

      runOptimizerAnalysis();
    });
  });

  // 3. Material Selection Dropdown
  const matSelect = document.getElementById("opt-material-select");
  if (matSelect) {
    matSelect.addEventListener("change", (e) => {
      optState.material = e.target.value;
      updateMaterialPreview(e.target.value);
      runOptimizerAnalysis();
    });
  }

  // 4. Required Finish Pills
  document.querySelectorAll(".finish-pill-btn").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".finish-pill-btn").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      optState.finish = pill.dataset.finish || "standard";
      runOptimizerAnalysis();
    });
  });

  // 5. G-Code & Path Smoothing Checkboxes
  ["opt-chk-arc-fitting", "opt-chk-micro-filter", "opt-chk-collinear-merge", "opt-chk-corner-decel"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", runOptimizerAnalysis);
  });

  // 6. Action Buttons
  const btnRecalc = document.getElementById("btn-opt-recalc");
  if (btnRecalc) btnRecalc.addEventListener("click", runOptimizerAnalysis);
  const btnApply = document.getElementById("btn-opt-apply");
  if (btnApply) btnApply.addEventListener("click", applyOptimizerToolpath);
}

// Switch between Manual CAM Studio and Auto-Machining Optimizer with Apple Liquid Glass Slider
function switchStudioTab(tabId) {
  const tabManual = document.getElementById("tab-btn-manual");
  const tabAuto = document.getElementById("tab-btn-auto-optimizer");
  const sideManual = document.getElementById("sidebar-manual-cam");
  const sideAuto = document.getElementById("sidebar-auto-optimizer");

  let activeBtn = tabManual;
  if (tabId === "tab-auto-optimizer") {
    if (tabManual) tabManual.classList.remove("active");
    if (tabAuto) tabAuto.classList.add("active");
    if (sideManual) sideManual.style.display = "none";
    if (sideAuto) sideAuto.style.display = "flex";
    activeBtn = tabAuto;
    runOptimizerAnalysis();
  } else {
    if (tabAuto) tabAuto.classList.remove("active");
    if (tabManual) tabManual.classList.add("active");
    if (sideAuto) sideAuto.style.display = "none";
    if (sideManual) sideManual.style.display = "flex";
    activeBtn = tabManual;
  }

  updateStudioGlassPill(activeBtn);

  // Ensure Three.js viewport matches current layout
  setTimeout(onWindowResize, 10);
}

// Update material physical properties preview
function updateMaterialPreview(matKey) {
  const preview = document.getElementById("opt-mat-preview");
  if (!preview) return;

  const matData = {
    wood_mdf: { density: "0.75 g/cm³", coolant: "Dry Dust Extr.", chip: "0.04–0.12 mm/t" },
    plastic_delrin: { density: "1.41 g/cm³", coolant: "Cold Air / Dry", chip: "0.035–0.09 mm/t" },
    aluminum_6061: { density: "2.70 g/cm³", coolant: "Alcohol Mist / WD40", chip: "0.02–0.055 mm/t" },
    steel_mild: { density: "7.85 g/cm³", coolant: "Flood Coolant / Mist", chip: "0.012–0.03 mm/t" }
  };

  const info = matData[matKey] || matData.wood_mdf;
  preview.innerHTML = `
    <span class="mat-prop-item"><b>Density:</b> ${info.density}</span>
    <span class="mat-prop-item"><b>Coolant:</b> ${info.coolant}</span>
    <span class="mat-prop-item"><b>Chipload:</b> ${info.chip}</span>
  `;
}

// Fetch and render multi-objective trade-off metrics
async function runOptimizerAnalysis() {
  setStatus("Auto-Machining Optimizer: Calculating Pareto trade-offs across speed, tool life & finish...", "busy");

  try {
    const payload = {
      profile: optState.profile,
      material: optState.material,
      finish: optState.finish,
      gcode_opts: {
        arc_fitting: document.getElementById("opt-chk-arc-fitting") ? document.getElementById("opt-chk-arc-fitting").checked : true,
        micro_filter: document.getElementById("opt-chk-micro-filter") ? document.getElementById("opt-chk-micro-filter").checked : true,
        collinear_merge: document.getElementById("opt-chk-collinear-merge") ? document.getElementById("opt-chk-collinear-merge").checked : true,
        corner_decel: document.getElementById("opt-chk-corner-decel") ? document.getElementById("opt-chk-corner-decel").checked : true
      },
      machine_id: currentMachine ? currentMachine.id : "genmitsu_3018",
      rot_x: parseFloat(document.getElementById("tf-rot-x") ? document.getElementById("tf-rot-x").value : 0),
      rot_y: parseFloat(document.getElementById("tf-rot-y") ? document.getElementById("tf-rot-y").value : 0),
      rot_z: parseFloat(document.getElementById("tf-rot-z") ? document.getElementById("tf-rot-z").value : 0),
      scale_x: parseFloat(document.getElementById("tf-scale-x") ? document.getElementById("tf-scale-x").value : 100) / 100,
      scale_y: parseFloat(document.getElementById("tf-scale-y") ? document.getElementById("tf-scale-y").value : 100) / 100,
      scale_z: parseFloat(document.getElementById("tf-scale-z") ? document.getElementById("tf-scale-z").value : 100) / 100
    };

    const res = await fetch("/api/optimizer/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) {
      setStatus(`Optimizer: ${data.error || "Failed to analyze"}`, "error");
      return;
    }

    latestOptimizerData = data;
    renderOptimizerAnalysis(data);
    setStatus("Optimizer analysis complete. Ready to apply toolpaths.", "ready");
  } catch (err) {
    console.error("Optimizer analysis error:", err);
    setStatus("Optimizer analysis failed.", "error");
  }
}

// Render Comparative Metrics & Engineering Rationales
function renderOptimizerAnalysis(data) {
  const base = data.baseline;
  const opt = data.optimized;
  const imp = data.improvements;

  // 1. Hero Summary Pill & Header Status
  const summaryText = document.getElementById("opt-summary-text");
  if (summaryText) summaryText.textContent = data.summary_pill;
  const statusProfile = document.getElementById("opt-status-profile");
  if (statusProfile) statusProfile.textContent = data.profile.charAt(0).toUpperCase() + data.profile.slice(1);
  const statusMat = document.getElementById("opt-status-material");
  if (statusMat) statusMat.textContent = data.material_name;
  const statusTime = document.getElementById("opt-status-time");
  if (statusTime) statusTime.textContent = `${opt.cycle_time_min.toFixed(1)} min (-${imp.time_savings_pct}%)`;

  // 2. Comparison Matrix Cards
  const elBaseTime = document.getElementById("comp-base-time");
  if (elBaseTime) elBaseTime.textContent = `${base.cycle_time_min.toFixed(1)} min`;
  const elOptTime = document.getElementById("comp-opt-time");
  if (elOptTime) elOptTime.textContent = `${opt.cycle_time_min.toFixed(1)} min`;
  const elDeltaTime = document.getElementById("delta-time");
  if (elDeltaTime) elDeltaTime.textContent = `-${imp.time_savings_pct}% Cycle Time`;

  const elBaseTool = document.getElementById("comp-base-tool");
  if (elBaseTool) elBaseTool.textContent = `${base.tool_life_score_pct.toFixed(0)}%`;
  const elOptTool = document.getElementById("comp-opt-tool");
  if (elOptTool) elOptTool.textContent = `${opt.tool_life_score_pct.toFixed(0)}%`;
  const elDeltaTool = document.getElementById("delta-tool");
  if (elDeltaTool) elDeltaTool.textContent = `+${imp.tool_life_gain_pct}% Longevity`;

  const elBaseScallop = document.getElementById("comp-base-scallop");
  if (elBaseScallop) elBaseScallop.textContent = `${base.scallop_height_um.toFixed(1)} µm`;
  const elOptScallop = document.getElementById("comp-opt-scallop");
  if (elOptScallop) elOptScallop.textContent = `${opt.scallop_height_um.toFixed(1)} µm`;
  const elDeltaScallop = document.getElementById("delta-scallop");
  if (elDeltaScallop) elDeltaScallop.textContent = `-${imp.scallop_reduction_pct}% Scallop`;

  const elBaseEnergy = document.getElementById("comp-base-energy");
  if (elBaseEnergy) elBaseEnergy.textContent = `${base.energy_consumption_kwh.toFixed(3)} kWh`;
  const elOptEnergy = document.getElementById("comp-opt-energy");
  if (elOptEnergy) elOptEnergy.textContent = `${opt.energy_consumption_kwh.toFixed(3)} kWh`;
  const elDeltaEnergy = document.getElementById("delta-energy");
  if (elDeltaEnergy) elDeltaEnergy.textContent = `-${imp.energy_savings_pct}% Power`;

  const elBaseLines = document.getElementById("comp-base-lines");
  if (elBaseLines) elBaseLines.textContent = `${base.gcode_estimated_lines.toLocaleString()} lines (${base.micro_moves_count.toLocaleString()} micro)`;
  const elOptLines = document.getElementById("comp-opt-lines");
  if (elOptLines) elOptLines.textContent = `${opt.gcode_estimated_lines.toLocaleString()} lines (${opt.micro_moves_count} micro)`;
  const elDeltaLines = document.getElementById("delta-lines");
  if (elDeltaLines) elDeltaLines.textContent = `-${imp.gcode_size_reduction_pct}% G-Code Compression`;

  // 3. The 4 Engineering Rationales
  const rats = data.rationales;
  const elRatTime = document.getElementById("rat-time-text");
  if (elRatTime) elRatTime.textContent = rats.machining_time;
  const elRatTool = document.getElementById("rat-tool-text");
  if (elRatTool) elRatTool.textContent = rats.tool_life;
  const elRatSurf = document.getElementById("rat-surface-text");
  if (elRatSurf) elRatSurf.textContent = rats.surface_quality;
  const elRatProd = document.getElementById("rat-prod-text");
  if (elRatProd) elRatProd.textContent = rats.productivity;

  // 4. Update 3D Cutter preview diameter
  updateToolMesh(data.tool_diameter_mm || 3.175);

  // 5. Fastest Mode Tool Breakage Warning Banner Toggle
  const warnBanner = document.getElementById("opt-fastest-warning");
  if (warnBanner) {
    if (data.profile === "fastest" || data.profile_warning) {
      warnBanner.style.display = "flex";
      const descEl = warnBanner.querySelector(".warn-banner-desc");
      if (descEl && data.profile_warning) {
        descEl.textContent = data.profile_warning;
      }
    } else {
      warnBanner.style.display = "none";
    }
  }
}

// 1-Click: Optimize, Slice, Fit G2/G3 Arcs & Render 3D Toolpaths
async function applyOptimizerToolpath() {
  const applyBtn = document.getElementById("btn-opt-apply");
  if (applyBtn) {
    applyBtn.disabled = true;
    applyBtn.textContent = "Optimizing & Slicing...";
  }

  setStatus("Auto-Machining Optimizer: Generating optimized multi-pass clearing and fitting G2/G3 circular arcs...", "busy");

  try {
    const payload = {
      profile: optState.profile,
      material: optState.material,
      finish: optState.finish,
      gcode_opts: {
        arc_fitting: document.getElementById("opt-chk-arc-fitting") ? document.getElementById("opt-chk-arc-fitting").checked : true,
        micro_filter: document.getElementById("opt-chk-micro-filter") ? document.getElementById("opt-chk-micro-filter").checked : true,
        collinear_merge: document.getElementById("opt-chk-collinear-merge") ? document.getElementById("opt-chk-collinear-merge").checked : true,
        corner_decel: document.getElementById("opt-chk-corner-decel") ? document.getElementById("opt-chk-corner-decel").checked : true
      },
      machine_id: currentMachine ? currentMachine.id : "genmitsu_3018",
      stock_shape: document.getElementById("stock-shape") ? document.getElementById("stock-shape").value : "box",
      stock_w: parseFloat(document.getElementById("stock-w") ? document.getElementById("stock-w").value : 70),
      stock_l: parseFloat(document.getElementById("stock-l") ? document.getElementById("stock-l").value : 70),
      stock_t: parseFloat(document.getElementById("stock-t") ? document.getElementById("stock-t").value : 32),
      stock_d: parseFloat(document.getElementById("stock-d") ? document.getElementById("stock-d").value : 90),
      pos_x: parseFloat(document.getElementById("tf-pos-x") ? document.getElementById("tf-pos-x").value : 0),
      pos_y: parseFloat(document.getElementById("tf-pos-y") ? document.getElementById("tf-pos-y").value : 0),
      pos_z: parseFloat(document.getElementById("tf-pos-z") ? document.getElementById("tf-pos-z").value : 0),
      rot_x: parseFloat(document.getElementById("tf-rot-x") ? document.getElementById("tf-rot-x").value : 0),
      rot_y: parseFloat(document.getElementById("tf-rot-y") ? document.getElementById("tf-rot-y").value : 0),
      rot_z: parseFloat(document.getElementById("tf-rot-z") ? document.getElementById("tf-rot-z").value : 0),
      scale_x: parseFloat(document.getElementById("tf-scale-x") ? document.getElementById("tf-scale-x").value : 100) / 100,
      scale_y: parseFloat(document.getElementById("tf-scale-y") ? document.getElementById("tf-scale-y").value : 100) / 100,
      scale_z: parseFloat(document.getElementById("tf-scale-z") ? document.getElementById("tf-scale-z").value : 100) / 100
    };

    const res = await fetch("/api/optimizer/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) {
      alert("Optimization failed: " + (data.error || "Unknown error"));
      setStatus(`Optimization failed: ${data.error || "Error"}`, "error");
      return;
    }

    currentSlicingData = data;

    // Render 3D layers in Three.js viewport
    renderSlicedLayers(data.layers);
    updateLayerSlider(data.layer_count);

    // Update manual statistics display as well
    document.getElementById("stat-layers").textContent = data.layer_count;
    document.getElementById("stat-cut-dist").textContent = `${data.cut_distance_mm.toFixed(0)} mm`;
    document.getElementById("stat-rapid-dist").textContent = `${data.rapid_distance_mm.toFixed(0)} mm`;
    document.getElementById("stat-est-time").textContent = `${data.estimated_minutes.toFixed(1)} min`;

    // Synchronize manual inputs with pulse highlights for overrides
    if (data.recommendation) {
      applyAutoModeRecommendations(data.recommendation);
    }

    // Enable G-code inspection & download buttons
    document.getElementById("btn-view-gcode").disabled = false;
    document.getElementById("btn-export-gcode").disabled = false;

    // Update status banner
    const gs = data.gcode_stats;
    setStatus(`Optimization Applied: Sliced in ${data.estimated_minutes.toFixed(1)} min. Fitted ${gs.fitted_arcs_count} G2/G3 arcs, eliminated ${gs.micro_moves_eliminated} micro-moves (${gs.reduction_pct}% G-code reduction).`, "ready");

    // Visual pulse on the View G-Code button
    const viewBtn = document.getElementById("btn-view-gcode");
    if (viewBtn) {
      viewBtn.classList.add("auto-highlight");
      setTimeout(() => viewBtn.classList.remove("auto-highlight"), 2500);
    }
  } catch (err) {
    console.error("Apply optimizer failed:", err);
    setStatus("Apply optimizer failed. Check console.", "error");
  } finally {
    if (applyBtn) {
      applyBtn.disabled = false;
      applyBtn.innerHTML = '<span class="icon"><svg class="svg-icon" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg></span> Optimize & Apply Toolpath';
    }
  }
}


// =========================================================================
// Apple Liquid Glass Segmented Controls & Theme Switcher System
// =========================================================================
function updateStudioGlassPill(activeBtn) {
  const pill = document.getElementById("studio-glass-pill");
  if (!pill || !activeBtn) return;
  pill.style.transform = `translate(${activeBtn.offsetLeft}px, ${activeBtn.offsetTop}px)`;
  pill.style.width = `${activeBtn.offsetWidth}px`;
  pill.style.height = `${activeBtn.offsetHeight}px`;
}

function setupStudioGlassSlider() {
  const manualBtn = document.getElementById("tab-btn-manual");
  const refresh = () => {
    const active = document.querySelector(".studio-tab.active") || manualBtn;
    if (active) updateStudioGlassPill(active);
  };
  setTimeout(refresh, 50);
  setTimeout(refresh, 200);
  window.addEventListener("load", refresh);
  window.addEventListener("resize", refresh);
}

function setupThemeSwitching() {
  const btnDark = document.getElementById("btn-theme-dark");
  const btnLight = document.getElementById("btn-theme-light");
  const themePill = document.getElementById("theme-glass-pill");

  function updateThemePill(btn) {
    if (!btn || !themePill) return;
    themePill.style.transform = `translate(${btn.offsetLeft}px, ${btn.offsetTop}px)`;
    themePill.style.width = `${btn.offsetWidth}px`;
    themePill.style.height = `${btn.offsetHeight}px`;
  }

  function applyTheme(themeName) {
    const isLight = themeName === "light";
    document.body.classList.toggle("light-theme", isLight);
    if (btnDark) btnDark.classList.toggle("active", !isLight);
    if (btnLight) btnLight.classList.toggle("active", isLight);
    updateThemePill(isLight ? btnLight : btnDark);
    localStorage.setItem("cnc_slicer_theme", themeName);

    // Refresh studio glass pill position
    const activeStudioTab = document.querySelector(".studio-tab.active");
    if (activeStudioTab) {
      updateStudioGlassPill(activeStudioTab);
    }

    // Update Three.js scene background if initialized
    if (typeof scene !== "undefined" && scene) {
      scene.background = new THREE.Color(isLight ? 0xe2e8f0 : 0x080b11);
    }
  }

  if (btnDark) {
    btnDark.addEventListener("click", () => applyTheme("dark"));
  }
  if (btnLight) {
    btnLight.addEventListener("click", () => applyTheme("light"));
  }

  const savedTheme = localStorage.getItem("cnc_slicer_theme") || "dark";
  setTimeout(() => {
    applyTheme(savedTheme);
  }, 50);

  window.addEventListener("resize", () => {
    const isLight = document.body.classList.contains("light-theme");
    updateThemePill(isLight ? btnLight : btnDark);
  });
}
