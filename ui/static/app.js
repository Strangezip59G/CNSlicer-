// CNC Milling Slicer — Interactive 3D WebGL Studio
let scene, camera, renderer, controls;
let bedGrid, stockMesh, partMeshGroup, toolpathLinesGroup;
let currentSlicingData = null;
let currentModelType = "sample1";
let isSimulating = false;
let simTimer = null;

// Initialize Three.js 3D Viewport
function initViewport() {
  const container = document.getElementById("viewport-3d");
  const width = container.clientWidth;
  const height = container.clientHeight;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x121214);

  // Perspective camera
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
  camera.position.set(160, -220, 180);
  camera.up.set(0, 0, 1); // Z is UP in CNC machine coordinates!

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(window.devicePixelRatio);
  container.appendChild(renderer.domElement);

  // Orbit controls
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.target.set(60, 40, 10);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;

  // Lighting
  const ambLight = new THREE.AmbientLight(0xffffff, 0.6);
  scene.add(ambLight);

  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight1.position.set(200, 200, 300);
  scene.add(dirLight1);

  const dirLight2 = new THREE.DirectionalLight(0x90caf9, 0.4);
  dirLight2.position.set(-200, -200, 100);
  scene.add(dirLight2);

  // Spoilboard Bed (400 x 400 mm)
  createBedGrid(400, 400);

  // Groups
  partMeshGroup = new THREE.Group();
  toolpathLinesGroup = new THREE.Group();
  scene.add(partMeshGroup);
  scene.add(toolpathLinesGroup);

  // Initial default stock block
  updateStockMesh(100, 80, 20, 50, 50);

  // Load default Sample 1 model
  loadSample1Model();

  // Resize handler
  window.addEventListener("resize", onWindowResize);

  animate();
}

function onWindowResize() {
  const container = document.getElementById("viewport-3d");
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}

function createBedGrid(w, l) {
  if (bedGrid) scene.remove(bedGrid);

  const gridGroup = new THREE.Group();

  // Base Spoilboard Plate
  const plateGeo = new THREE.PlaneGeometry(w, l);
  const plateMat = new THREE.MeshStandardMaterial({
    color: 0x1e1e22,
    roughness: 0.9,
    metalness: 0.1
  });
  const plate = new THREE.Mesh(plateGeo, plateMat);
  plate.position.set(w / 2, l / 2, -0.2);
  gridGroup.add(plate);

  // Grid lines
  const grid = new THREE.GridHelper(w, 40, 0x44444c, 0x2a2a30);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(w / 2, l / 2, 0);
  gridGroup.add(grid);

  // Machine Axes helper
  const axes = new THREE.AxesHelper(50);
  axes.position.set(0, 0, 0);
  gridGroup.add(axes);

  bedGrid = gridGroup;
  scene.add(bedGrid);
}

function updateStockMesh(w, l, t, x0 = 50, y0 = 50) {
  if (stockMesh) scene.remove(stockMesh);

  const geo = new THREE.BoxGeometry(w, l, t);
  const mat = new THREE.MeshPhysicalMaterial({
    color: 0x10b981, // Emerald green
    transparent: true,
    opacity: 0.28,
    roughness: 0.3,
    transmission: 0.6,
    thickness: 1.0,
    depthWrite: false
  });

  stockMesh = new THREE.Mesh(geo, mat);
  // Position so bottom sits at Z=0 (spoilboard)
  stockMesh.position.set(x0 + w / 2, y0 + l / 2, t / 2);

  // Add subtle edge wireframe
  const edges = new THREE.EdgesGeometry(geo);
  const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });
  const wire = new THREE.LineSegments(edges, lineMat);
  stockMesh.add(wire);

  scene.add(stockMesh);
  controls.target.set(x0 + w / 2, y0 + l / 2, t / 2);
}

// Sample 1: Rectangular block with circular hole & oblong slot
function loadSample1Model() {
  currentModelType = "sample1";
  while (partMeshGroup.children.length > 0) {
    partMeshGroup.remove(partMeshGroup.children[0]);
  }

  const stockW = parseFloat(document.getElementById("stock-w").value);
  const stockL = parseFloat(document.getElementById("stock-l").value);
  const stockT = parseFloat(document.getElementById("stock-t").value);

  // Part body inside stock (with 5mm margins)
  const partW = stockW - 10;
  const partL = stockL - 10;
  const partT = stockT - 2;

  const shape = new THREE.Shape();
  shape.moveTo(0, 0);
  shape.lineTo(partW, 0);
  shape.lineTo(partW, partL);
  shape.lineTo(0, partL);
  shape.lineTo(0, 0);

  // Circular hole cutout (hole 1)
  const holePath = new THREE.Path();
  holePath.absarc(25, partL / 2, 8, 0, Math.PI * 2, true);
  shape.holes.push(holePath);

  // Oblong slot cutout (hole 2)
  const slotPath = new THREE.Path();
  const sx = partW - 35, sy = partL / 2, sw = 20, sh = 10;
  slotPath.moveTo(sx, sy - sh / 2);
  slotPath.lineTo(sx + sw, sy - sh / 2);
  slotPath.absarc(sx + sw, sy, sh / 2, -Math.PI / 2, Math.PI / 2, false);
  slotPath.lineTo(sx, sy + sh / 2);
  slotPath.absarc(sx, sy, sh / 2, Math.PI / 2, -Math.PI / 2, false);
  shape.holes.push(slotPath);

  const extrudeSettings = { depth: partT, bevelEnabled: false };
  const geo = new THREE.ExtrudeGeometry(shape, extrudeSettings);
  const mat = new THREE.MeshStandardMaterial({
    color: 0x93c5fd, // Light steel blue
    roughness: 0.4,
    metalness: 0.6
  });

  const partMesh = new THREE.Mesh(geo, mat);
  partMesh.position.set(55, 55, 0);
  partMeshGroup.add(partMesh);

  document.getElementById("status-text").textContent = "Sample 1 loaded: Solid block with circular through-hole and oblong slot.";
}

// Sample 2: 3-Tier Stepped Pyramid from solid billet
function loadSample2Model() {
  currentModelType = "sample2";
  while (partMeshGroup.children.length > 0) {
    partMeshGroup.remove(partMeshGroup.children[0]);
  }

  const stockW = 60, stockL = 60, stockT = 30;
  document.getElementById("stock-w").value = stockW;
  document.getElementById("stock-l").value = stockL;
  document.getElementById("stock-t").value = stockT;
  updateStockMesh(stockW, stockL, stockT, 50, 50);

  const mat = new THREE.MeshStandardMaterial({
    color: 0xf59e0b, // Amber gold
    roughness: 0.3,
    metalness: 0.7
  });

  // Base tier 3: 60x60x10
  const b3 = new THREE.Mesh(new THREE.BoxGeometry(60, 60, 10), mat);
  b3.position.set(80, 80, 5);
  partMeshGroup.add(b3);

  // Mid tier 2: 40x40x10
  const b2 = new THREE.Mesh(new THREE.BoxGeometry(40, 40, 10), mat);
  b2.position.set(80, 80, 15);
  partMeshGroup.add(b2);

  // Top tier 1: 20x20x10
  const b1 = new THREE.Mesh(new THREE.BoxGeometry(20, 20, 10), mat);
  b1.position.set(80, 80, 25);
  partMeshGroup.add(b1);

  document.getElementById("status-text").textContent = "Sample 2 loaded: 3-Tier Stepped Pyramid to be carved out of solid raw billet.";
}

// Trigger LiDAR Bed Sweep Simulation
async function autoScanBedLidar() {
  const badge = document.getElementById("lidar-status-badge");
  badge.textContent = "Scanning Bed...";
  badge.style.background = "#7e22ce";

  document.getElementById("status-text").textContent = "Toolhead LiDAR sweeping bed at 3000 mm/min to discover stock boundary...";

  // Call backend scan endpoint
  try {
    const res = await fetch("/api/lidar/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bed_w: 400,
        bed_l: 400,
        stock_x: 60 + Math.random() * 40,
        stock_y: 60 + Math.random() * 40,
        stock_w: 120,
        stock_l: 90,
        stock_t: 22.5
      })
    });
    const data = await res.json();

    if (data.detected) {
      document.getElementById("stock-w").value = data.width.toFixed(1);
      document.getElementById("stock-l").value = data.length.toFixed(1);
      document.getElementById("stock-t").value = data.thickness.toFixed(1);
      document.getElementById("stock-pos").value = `X: ${data.x_min.toFixed(1)}, Y: ${data.y_min.toFixed(1)}`;
      document.getElementById("surface-warp-val").textContent = `${data.max_deviation.toFixed(2)} mm (Auto-Leveled)`;

      updateStockMesh(data.width, data.length, data.thickness, data.x_min, data.y_min);

      badge.textContent = "Stock Located (Auto)";
      badge.style.background = "#065f46";
      document.getElementById("status-text").textContent = `LiDAR Auto-Detection Succeeded: Found ${data.width.toFixed(1)} x ${data.length.toFixed(1)} x ${data.thickness.toFixed(1)} mm stock.`;
    }
  } catch (err) {
    console.error("Scan error:", err);
    badge.textContent = "Scan Offline (Mock)";
  }
}

// Execute Slicing on Backend
async function sliceCurrentModel() {
  const sliceBtn = document.getElementById("btn-slice");
  sliceBtn.disabled = true;
  sliceBtn.textContent = "Slicing...";
  document.getElementById("status-text").textContent = "Calculating Subtractive Slices: Stock(Z) - Part(Z)...";

  try {
    const payload = {
      model_type: currentModelType,
      stock_w: parseFloat(document.getElementById("stock-w").value),
      stock_l: parseFloat(document.getElementById("stock-l").value),
      stock_t: parseFloat(document.getElementById("stock-t").value),
      stepdown: parseFloat(document.getElementById("cut-stepdown").value),
      stepover: parseFloat(document.getElementById("cut-stepover").value) / 100.0,
      feed_xy: parseFloat(document.getElementById("feed-xy").value),
      feed_z: parseFloat(document.getElementById("feed-z").value),
      tool_d: document.getElementById("tool-select").value === "flat_1_4" ? 6.35 : 3.175
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

    // Configure vertical layer scrubber
    const slider = document.getElementById("layer-slider");
    slider.min = "0";
    slider.max = String(data.layer_count);
    slider.value = String(data.layer_count);
    document.getElementById("current-layer-label").textContent = `1 - ${data.layer_count}`;

    // Render toolpaths in 3D
    renderToolpaths(data.layers);

    document.getElementById("btn-export-gcode").disabled = false;
    document.getElementById("status-text").textContent = `Slicing complete: ${data.layer_count} depth passes generated. Scrub slider to inspect layers.`;
  } catch (err) {
    console.error("Slicing failed:", err);
    document.getElementById("status-text").textContent = "Slicing failed. Check console log.";
  } finally {
    sliceBtn.disabled = false;
    sliceBtn.textContent = "🔪 Slice Stock";
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

    lyr.segments.forEach(seg => {
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

// Handle vertical layer slider scrubbing
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

// Export G-Code
async function exportGCode() {
  if (!currentSlicingData) return;
  window.location.href = "/api/gcode/download";
}

// Camera Preset Views
function setCameraView(view) {
  const stockW = parseFloat(document.getElementById("stock-w").value);
  const stockL = parseFloat(document.getElementById("stock-l").value);
  const stockT = parseFloat(document.getElementById("stock-t").value);
  const cx = 50 + stockW / 2;
  const cy = 50 + stockL / 2;
  const cz = stockT / 2;

  controls.target.set(cx, cy, cz);

  if (view === "top") {
    camera.position.set(cx, cy, cz + 250);
  } else if (view === "front") {
    camera.position.set(cx, cy - 250, cz);
  } else if (view === "iso") {
    camera.position.set(cx + 140, cy - 180, cz + 140);
  }
}

// Animation loop
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// Bind UI Events on Load
window.addEventListener("DOMContentLoaded", () => {
  initViewport();

  document.getElementById("btn-scan-lidar").addEventListener("click", autoScanBedLidar);
  document.getElementById("btn-load-sample1").addEventListener("click", loadSample1Model);
  document.getElementById("btn-load-sample2").addEventListener("click", loadSample2Model);
  document.getElementById("btn-slice").addEventListener("click", sliceCurrentModel);
  document.getElementById("btn-export-gcode").addEventListener("click", exportGCode);

  document.getElementById("layer-slider").addEventListener("input", onLayerSliderInput);

  document.getElementById("btn-view-top").addEventListener("click", () => setCameraView("top"));
  document.getElementById("btn-view-front").addEventListener("click", () => setCameraView("front"));
  document.getElementById("btn-view-iso").addEventListener("click", () => setCameraView("iso"));
  document.getElementById("btn-reset-cam").addEventListener("click", () => setCameraView("iso"));
});
