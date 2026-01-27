"use client";

import { Database } from "@/lib/database.types";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { useState, useEffect, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import createREGL from "regl";

type SCRNAGene = {
  gene_name: string;
};

type SCRNACell = {
  cluster_id: number | null;
  cell_number: number | null;
  x: number | null;
  y: number | null;
};

// List of 30 pastel colors for different clusters
const clusterColors = [
  [1.0, 0.4352, 0.3803, 1],
  [0.4196, 0.3568, 0.5843, 1],
  [0.5333, 0.6901, 0.2941, 1],
  [0.9686, 0.7922, 0.7882, 1],
  [0.5725, 0.6588, 0.8196, 1],
  [0.5843, 0.3216, 0.3176, 1],
  [0.7098, 0.3961, 0.6549, 1],
  [0, 0.6078, 0.4667, 1],
  [0.8667, 0.2549, 0.1412, 1],
  [0.8353, 0.3137, 0.4627, 1],
  [0.2706, 0.7216, 0.6745, 1],
  [0.9373, 0.7529, 0.3137, 1],
  [0.6078, 0.1373, 0.2078, 1],
  [0.8745, 0.8118, 0.7451, 1],
  [0.3333, 0.7059, 0.6902, 1],
  [0.8902, 0.3647, 0.2667, 1],
  [0.498, 0.7804, 0.6863, 1],
  [0.8314, 0.6588, 0.4157, 1],
  [0.6196, 0.0627, 0.1882, 1],
  [0.9569, 0.6431, 0.2824, 1],
  [0.2627, 0.5647, 0.5333, 1],
  [0.9686, 0.6078, 0.4784, 1],
  [0.3765, 0.6824, 0.6118, 1],
  [0.9922, 0.7059, 0.4863, 1],
  [0.5569, 0.4745, 0.5725, 1],
  [0.9922, 0.6078, 0.6667, 1],
  [0.3686, 0.6078, 0.5569, 1],
  [0.9922, 0.6824, 0.6078, 1],
  [0.4353, 0.5569, 0.6078, 1],
  [0.9922, 0.7569, 0.6471, 1],
  [0.4902, 0.5059, 0.6078, 1],
  [0.9922, 0.8314, 0.6863, 1],
  [0.5451, 0.4509, 0.6078, 1],
  [0.9922, 0.9059, 0.7255, 1],
  [0.6, 0.3961, 0.6078, 1],
  [0.9922, 0.9569, 0.7647, 1],
  [0.6549, 0.4509, 0.6078, 1],
  [0.9922, 0.9569, 0.8, 1],
  [0.7098, 0.5059, 0.6078, 1],
  [0.9922, 0.9569, 0.8314, 1],
];

export default function ExpressionDataset({ name }: { name: string }) {
  const [genes, setGenes] = useState<SCRNAGene[] | null>(null);
  const [cells, setCells] = useState<SCRNACell[] | null>(null);
  const [numClusters, setNumClusters] = useState<number | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [selectedGene, setSelectedGene] = useState<string | null>(null);
  const [counts, setCounts] = useState<any | null>({});
  const [is3D, setIs3D] = useState<boolean>(false);
  const [isDarkMode, setIsDarkMode] = useState<boolean>(false);

  useEffect(() => {
    const fetchData = async () => {
      const supabase = createClientSupabaseClient();
      supabase
        .from("scrna_datasets")
        .select(
          "*, scrna_genes(gene_name), scrna_cells(x, y, cluster_id, cell_number)"
        )
        .eq("name", name)
        .single<{
          scrna_genes: SCRNAGene[];
          scrna_cells: SCRNACell[];
        }>()
        .then((data) => {
          const dataset = data?.data!;
          console.log("received data");
          setGenes(dataset.scrna_genes);
          setCells(dataset.scrna_cells);
        });
    };
    fetchData();
  }, [name]);

  useEffect(() => {
    const fetchData = async () => {
      const supabase = createClientSupabaseClient();
      // get the counts
      supabase.storage
        .from("scrna")
        .download(`counts/${name}/${selectedGene}.json`)
        .then((data) => {
          data.data?.text().then((text) => {
            const counts = JSON.parse(text);
            setCounts(counts);
          });
        });
    };
    if (selectedGene) {
      fetchData();
    }
  }, [name, selectedGene]);

  useEffect(() => {
    if (cells) {
      const numClusters = new Set(cells.map((cell) => cell.cluster_id)).size;
      setNumClusters(numClusters);
    }
  }, [cells]);

  useEffect(() => {
    document.addEventListener("mousedown", (e) => {
      setSelectedCluster(null);
    });
    return () => {
      document.removeEventListener("mousedown", (e) => {
        setSelectedCluster(null);
      });
    };
  });

  // Define points
  const xAbsMax =
    cells?.reduce(
      (acc, cell) => (Math.abs(cell.x!) > acc ? Math.abs(cell.x!) : acc),
      0
    ) || 1;
  const yAbsMax =
    cells?.reduce(
      (acc, cell) => (Math.abs(cell.y!) > acc ? Math.abs(cell.y!) : acc),
      0
    ) || 1;

  const scale = 0.9 / Math.max(xAbsMax, yAbsMax);

  const position = cells?.map((cell) => [cell.x! * scale, cell.y! * scale]);

  // Define colors
  const clusterColor = cells?.map((cell) => {
    const colorIdx = cell.cluster_id! % clusterColors.length;
    const color = clusterColors[colorIdx];
    return selectedCluster === null
      ? color
      : cell.cluster_id === selectedCluster
      ? color
      : [0.9, 0.9, 0.9, 1.0];
  });

  return (
    <div>
      {genes ? (
        <div>
          <div className="text-xs font-bold">Gene ID</div>
          <Autocomplete
            values={genes.map((gene) => gene.gene_name)}
            onSelect={(gene) => {
              setSelectedGene(gene);
            }}
          />

          {/* Controls */}
          <div className="flex gap-4 mt-4 mb-4">
            <button
              onClick={() => setIs3D(!is3D)}
              className="px-4 py-2 rounded-md border border-gray-300 hover:bg-gray-100 transition-colors"
            >
              {is3D ? "Switch to 2D" : "Switch to 3D"}
            </button>
            <button
              onClick={() => setIsDarkMode(!isDarkMode)}
              className="px-4 py-2 rounded-md border border-gray-300 hover:bg-gray-100 transition-colors"
            >
              {isDarkMode ? "Light Mode" : "Dark Mode"}
            </button>
          </div>

          {/* UMAP Visualization */}
          <div className="flex flex-row mt-4">
            <div className="mr-4">
              {cells ? (
                is3D ? (
                  <UMAPView3D
                    cells={cells}
                    position={position!}
                    color={clusterColor!}
                    scale={scale}
                    isDarkMode={isDarkMode}
                    selectedCluster={selectedCluster}
                  />
                ) : (
                  <UMAPView2D
                    position={position!}
                    color={clusterColor!}
                    isDarkMode={isDarkMode}
                    selectedCluster={selectedCluster}
                  />
                )
              ) : (
                <div>Loading...</div>
              )}
            </div>

            {/* Cluster Legend */}
            {numClusters ? (
              <div className="grid grid-rows-10 grid-flow-col ml-2">
                {clusterColors.slice(0, numClusters ?? -1).map((color, i) => (
                  <div
                    key={i}
                    className={
                      "pr-2 " +
                      (selectedCluster !== null
                        ? selectedCluster === i
                          ? ""
                          : "opacity-30"
                        : "")
                    }
                  >
                    <div
                      className="w-2 h-2 inline-block rounded-full mr-2"
                      style={{
                        backgroundColor: `rgba(${color[0] * 255}, ${
                          color[1] * 255
                        }, ${color[2] * 255}, ${color[3]})`,
                      }}
                    ></div>
                    <div
                      className={"inline text-xs cursor-pointer"}
                      onClick={(e) => {
                        e.stopPropagation();
                        console.log(`toggling cluster ${i}`);
                        if (selectedCluster === i) {
                          setSelectedCluster(null);
                        } else {
                          setSelectedCluster(i);
                        }
                      }}
                    >
                      Cluster {i}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div>Loading...</div>
      )}
    </div>
  );
}

// 2D UMAP View using REGL
function UMAPView2D({
  position,
  color,
  isDarkMode,
  selectedCluster,
}: {
  position: number[][];
  color: number[][];
  isDarkMode: boolean;
  selectedCluster: number | null;
}) {
  const reglContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    var regl: createREGL.Regl | null = null;
    if (reglContainerRef.current) {
      regl = createREGL({
        container: reglContainerRef.current,
      });

      const bgColor = isDarkMode ? [0.1, 0.1, 0.1, 1] : [1, 1, 1, 1];

      // Define the draw command
      const drawPoints = regl({
        frag: `
            precision mediump float;
            varying vec4 fragColor;
            void main() {
                gl_FragColor = fragColor;
            }`,

        vert: `
            precision mediump float;
            attribute vec2 position;
            attribute vec4 color;
            varying vec4 fragColor;
            void main() {
                gl_PointSize = ${selectedCluster !== null ? 4.0 : 3.0};
                gl_Position = vec4(position, 0, 1);
                fragColor = color;
            }`,

        attributes: {
          position: position,
          color: color,
        },

        count: position.length,

        primitive: "points",
      });

      // Render the points
      regl?.frame(() => {
        regl?.clear({
          color: bgColor,
          depth: 1,
        });

        drawPoints();
      });
    }

    // Cleanup function to stop regl's animation loop when the component unmounts
    return () => {
      regl && regl.destroy();
    };
  }, [position, color, isDarkMode, selectedCluster]);

  return (
    <div
      ref={reglContainerRef}
      className={`border rounded-md ${isDarkMode ? 'border-gray-700' : 'border-gray-300'}`}
      style={{ width: "600px", height: "600px" }}
    ></div>
  );
}

// 3D UMAP View using Three.js
function UMAPView3D({
  cells,
  position,
  color,
  scale,
  isDarkMode,
  selectedCluster,
}: {
  cells: SCRNACell[];
  position: number[][];
  color: number[][];
  scale: number;
  isDarkMode: boolean;
  selectedCluster: number | null;
}) {
  return (
    <div
      className={`border rounded-md ${isDarkMode ? 'border-gray-700' : 'border-gray-300'}`}
      style={{ width: "600px", height: "600px" }}
    >
      <Canvas camera={{ position: [0, 0, 3], fov: 50 }}>
        {isDarkMode ? (
          <color attach="background" args={[0.1, 0.1, 0.1]} />
        ) : (
          <color attach="background" args={[1, 1, 1]} />
        )}
        <ambientLight intensity={0.5} />
        <pointLight position={[10, 10, 10]} />
        <RotatingPoints
          cells={cells}
          position={position}
          color={color}
          scale={scale}
          selectedCluster={selectedCluster}
        />
        <OrbitControls enableDamping dampingFactor={0.05} />
      </Canvas>
    </div>
  );
}

// Rotating points component for 3D view
function RotatingPoints({
  cells,
  position,
  color,
  scale,
  selectedCluster,
}: {
  cells: SCRNACell[];
  position: number[][];
  color: number[][];
  scale: number;
  selectedCluster: number | null;
}) {
  const groupRef = useRef<THREE.Group>(null);

  // Rotate the entire group
  useFrame((state, delta) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * 0.2;
    }
  });

  return (
    <group ref={groupRef}>
      {cells.map((cell, i) => {
        const x = position[i][0];
        const y = position[i][1];
        // Add z-coordinate variation for 3D effect (can be random or based on cluster)
        const z = (Math.sin(x * 3) + Math.cos(y * 3)) * 0.1;

        const [r, g, b, a] = color[i];

        return (
          <mesh key={i} position={[x, y, z]}>
            <sphereGeometry args={[0.015, 8, 8]} />
            <meshStandardMaterial
              color={new THREE.Color(r, g, b)}
              emissive={new THREE.Color(r, g, b)}
              emissiveIntensity={0.5}
              toneMapped={false}
            />
          </mesh>
        );
      })}
    </group>
  );
}

function Autocomplete({
  values,
  onSelect,
}: {
  values: string[];
  onSelect: (value: string) => void;
}) {
  const [input, setInput] = useState("");
  const [focused, setFocused] = useState(false);
  const [filteredValues, setFilteredValues] = useState<string[]>([]);
  const [selectedIdx, setSelectedIdx] = useState<number>(0);

  useEffect(() => {
    setFilteredValues(
      values.filter((value) => value.startsWith(input))
    );
  }, [input, values]);

  return (
    <div>
      <input
        type="text"
        value={input}
        onChange={(e) => {
          if (e.target.value in values) {
            onSelect(e.target.value);
          }
          setInput(e.target.value);
        }}
        onFocus={() => {
          setFocused(true);
        }}
        onBlur={(e) => {
          setFocused(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            onSelect(filteredValues[selectedIdx]);
            setInput(filteredValues[selectedIdx]);
          }
          if (e.key === "ArrowDown") {
            setSelectedIdx((selectedIdx + 1) % filteredValues.length);
          }
          if (e.key === "ArrowUp") {
            setSelectedIdx(
              selectedIdx === 0 ? filteredValues.length - 1 : selectedIdx - 1
            );
          }
        }}
        className="border border-1 rounded-md p-2 w-60"
      />
      {filteredValues && focused && (
        <div className="border border-1 rounded-md p-1 w-60 absolute bg-white max-h-80 overflow-scroll z-10">
          {filteredValues.map((value, index) => (
            <div
              key={value}
              className={
                "hover:bg-gray-200 cursor-pointer p-1 rounded-md" +
                (index === selectedIdx ? " bg-gray-200" : "")
              }
              onMouseDown={() => {
                console.log(`selected ${value}`);
                onSelect(value);
                setInput(value);
              }}
            >
              {value}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
