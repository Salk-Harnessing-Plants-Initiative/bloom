import * as React from 'react';
import { useState, useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Node extends d3.SimulationNodeDatum {
    id: string;
    group: number;
    x: number;
    y: number;
}

interface Link {
    source: Node;
    target: Node;
}

interface NetworkGraphProps {
    data: {
        nodes: Node[];
        links: Link[];
    };
}

interface NetworkGraph{
    nodes: Node[];
    links: Link[];
}

export default function ExpressionCorrelation({ file_id }: { file_id: number }) {
    //   const [data, setData] = useState<boolean>(false);
    const svgRef = useRef<SVGSVGElement | null>(null);
    const [networkGraph, setNetworkGraph] = useState<NetworkGraph | null>(null);

    // useEffect(() => {
    //     fetch('/networkdata2.json')
    //         .then((response) => {
    //             if (!response.ok) {
    //                 throw new Error('Network response was not ok');
    //             }
    //             return response.json();
    //         })
    //         .then((data) => {
    //             setNetworkGraph(data);
    //         })
    //         .catch((error) => {
    //             console.error('There was a problem with the fetch operation:', error);
    //         });

    // }, [file_id]);

    // useEffect(() => {
    //     if (networkGraph) {
    //         if (!svgRef.current) return;
    //         const container = svgRef.current.getBoundingClientRect();
    //         const margin = { top: 10, right: 30, bottom: 30, left: 40 };
    //         const width = container.width - margin.left - margin.right;
    //         const height = 800;
    //         const colorScale = d3.scaleOrdinal(d3.schemeCategory10);

    //         const svg = d3.select(svgRef.current)
    //             .attr('width', width)
    //             .attr('height', height);

    //         const simulation = d3.forceSimulation(networkGraph?.nodes as Node[])
    //             .force('link', d3.forceLink<Node, Link>(networkGraph.links).id((d: Node) => d.id).distance(100))
    //             .force('charge', d3.forceManyBody().strength(-40))
    //             .force('center', d3.forceCenter(width / 2, height / 2))
    //             .force('collide', d3.forceCollide().radius(5))
    //             .force('boundary', d3.forceManyBody().strength(60))
    //             .force('boundary', forceBoundary(width, height))
    //             .on('tick', ticked);

    //         const link = svg.append('g')
    //             .selectAll('.link')
    //             .data(networkGraph.links)
    //             .enter().append('line')
    //             .attr('class', 'link')
    //             .attr('stroke', '#999')
    //             .attr('stroke-opacity', 0.6);

    //         const node = svg.append('g')
    //             .selectAll('.node')
    //             .data(networkGraph.nodes)
    //             .enter().append('circle')
    //             .attr('class', 'node')
    //             .attr('r', 10)
    //             .attr('fill', (d) => colorScale((d.group).toString()))
    //             .on("mouseover", handleMouseOver)
    //             .on("mouseout", handleMouseOut)
    //             .call(d3.drag<SVGCircleElement, Node>()
    //                 .on('start', dragStart)
    //                 .on('drag', dragging)
    //                 .on('end', dragEnd));

    //         const labels = svg.selectAll("text")
    //             .data(networkGraph.nodes)
    //             .enter().append("text")
    //             .attr("dy", -12)
    //             .attr("text-anchor", "middle")
    //             .attr("font-size", "10px")
    //             .attr("fill", "black")
    //             .text(d => d.id);

    //         // node.append('title')
    //         //     .text((d: Node) => d.id);

    //         // // Update positions of elements on each tick
    //         // simulation.on('tick', function () {
    //         //     link
    //         //         .attr('x1', (d: Link) => (d.source as Node).x)
    //         //         .attr('y1', (d: Link) => (d.source as Node).y)
    //         //         .attr('x2', (d: Link) => (d.target as Node).x)
    //         //         .attr('y2', (d: Link) => (d.target as Node).y);

    //         //     node
    //         //         .attr('cx', (d: Node) => d.x)
    //         //         .attr('cy', (d: Node) => d.y);
    //         // });

    //         // Dragging behavior
    //         // Update positions of elements on each tick

    //         function forceBoundary(width: number, height: number) {
    //             return function (alpha: number) {
    //                 networkGraph?.nodes.forEach((d: Node) => {
    //                     if (d.x < 0) d.x = 0;
    //                     if (d.y < 0) d.y = 0;
    //                     if (d.x > width) d.x = width;
    //                     if (d.y > height) d.y = height;
    //                 });
    //             };
    //         }

    //         function ticked() {
    //             link
    //                 .attr('x1', (d: Link) => d.source.x)
    //                 .attr('y1', (d: Link) => d.source.y)
    //                 .attr('x2', (d: Link) => d.target.x)
    //                 .attr('y2', (d: Link) => d.target.y);

    //             node
    //                 .attr('cx', (d: Node) => d.x)
    //                 .attr('cy', (d: Node) => d.y);

    //             labels
    //                 .attr("x", d => d.x)
    //                 .attr("y", d => d.y);
    //         }

    //         function dragStart(event: d3.D3DragEvent<SVGCircleElement, Node, any>) {
    //             if (!event.active) simulation.alphaTarget(0.3).restart();
    //             event.subject.fx = event.subject.x;
    //             event.subject.fy = event.subject.y;
    //         }

    //         function dragging(event: d3.D3DragEvent<SVGCircleElement, Node, any>) {
    //             event.subject.fx = event.x;
    //             event.subject.fy = event.y;
    //         }

    //         function dragEnd(event: d3.D3DragEvent<SVGCircleElement, Node, any>) {
    //             if (!event.active) simulation.alphaTarget(0);
    //             event.subject.fx = null;
    //             event.subject.fy = null;
    //         }

    //         function handleMouseOver(event: MouseEvent, d: { id: string, group: number }) {
    //             const connectedGroupId = d.group;

    //             node.attr("r", (n: Node) => n.group === connectedGroupId ? 15 : 10)
    //                 .attr("stroke", (n: Node) => n.group === connectedGroupId ? "black" : "none")
    //                 .attr("stroke-width", (n: Node) => n.group === connectedGroupId ? "3" : "none");

    //             // link.filter((l: Link) => d3.select(l.source).datum().group === connectedGroupId && d3.select(l.target).datum().group === connectedGroupId)
    //             //     .attr("stroke", "#999")
    //             //     .attr("stroke-width", 1);

    //             //single node highlight
    //             // d3.select(event.currentTarget as SVGCircleElement)
    //             //     .attr("r", 15)
    //             //     .attr("stroke", "black")
    //             //     .attr("stroke-width", 3);

    //             link.filter((l: Link) => l.source.id === d.id || l.target.id === d.id)
    //                 .attr("stroke", "black")
    //                 .attr("stroke-width", 4);
    //         }

    //         function handleMouseOut(event: MouseEvent, d: { id: string }) {

    //             node.attr("r", 10)
    //                 .attr("stroke", "none")
    //                 .attr("stroke-width", 0);

    //             d3.select(event.currentTarget as SVGCircleElement)
    //                 .attr("r", 10)
    //                 .attr("stroke", "none");

    //             link.attr("stroke", "#999").attr("stroke-width", 1);
    //         }
    //         // const zoom = d3.zoom()
    //         // .scaleExtent([0.1, 10])
    //         // .on('zoom', (event) => {
    //         //     d3.select(svgRef.current).attr('transform', event.transform);
    //         // });

    //         // d3.select<Element, unknown>(svgRef.current).call(zoom);

    //         return () => {
    //             svg.selectAll('*').remove();
    //         };
    //     }
    // }, [networkGraph]);

    return (
        <div>
        <center><h1>🚧 Under Construction 🚧</h1></center>
        </div>
        // <div>
        //     {/* <FullWidthTabs/> */}
        //     <div
        //         style={{
        //             boxSizing: 'border-box',
        //             gap: '20px',
        //             border: '1px solid rgb(62, 60, 60)',
        //             padding: '40px',
        //             borderRadius: '5px',
        //             marginLeft: 'auto',
        //             marginRight: 'auto',
        //             marginTop: '50px',
        //             alignItems: 'flex-start',
        //             minHeight: '800px',
        //             width: 'calc(100% - 100px)',
        //             maxWidth: '100%',
        //             overflow: 'hidden',
        //         }}
        //     >
        //         <h1 style={{ textAlign: 'center' }}>Co-expression Network</h1>
        //         <svg style={{ width: '100%', overflow: 'auto', }} ref={svgRef}></svg>
        //     </div>
        // </div>
    );
}
