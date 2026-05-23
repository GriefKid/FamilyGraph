window.GraphUI = {

init(nodes,edges,adj){

	this.createSearch(nodes)
	this.createStats(nodes,edges,adj)
	this.createPathFinder(nodes,adj)

},

createSearch(nodes){

	let input=document.createElement("input")

	input.placeholder="Search node"

	input.style.position="absolute"
	input.style.top="20px"
	input.style.left="20px"

	document.body.appendChild(input)

	input.addEventListener("input",e=>{

		let q=e.target.value.toLowerCase()

		let node=nodes.find(n=>n.username.toLowerCase().includes(q))

		if(node){

			offsetX = window.innerWidth/2 - node.x
			offsetY = window.innerHeight/2 - node.y

		}

	})

},

createStats(nodes,edges,adj){

	let panel=document.createElement("div")

	panel.style.position="absolute"
	panel.style.right="20px"
	panel.style.top="20px"
	panel.style.background="#0008"
	panel.style.padding="10px"
	panel.style.color="white"
	panel.style.borderRadius="8px"

	document.body.appendChild(panel)

	let basic=GraphStats.basic(nodes,edges)
	let degree=GraphStats.degreeDistribution(nodes,adj)
	let comp=GraphStats.connectedComponents(nodes,adj)
	let cluster=GraphStats.clusteringCoefficient(nodes,adj)

	panel.innerHTML=

	"Nodes: "+basic.nodes+"<br>"+
	"Edges: "+basic.edges+"<br>"+
	"Density: "+basic.density.toFixed(3)+"<br>"+
	"Degree avg: "+degree.avg.toFixed(2)+"<br>"+
	"Components: "+comp+"<br>"+
	"Clustering: "+cluster.toFixed(3)

},

createPathFinder(nodes,adj){

	let box=document.createElement("div")

	box.style.position="absolute"
	box.style.bottom="20px"
	box.style.left="20px"
	box.style.background="#0008"
	box.style.padding="10px"
	box.style.borderRadius="8px"

	box.innerHTML=

	'<input id="startNode" placeholder="start">'+
	'<input id="endNode" placeholder="end">'+
	'<button id="findPath">Find</button>'

	document.body.appendChild(box)

	document.getElementById("findPath").onclick=()=>{

		let a=document.getElementById("startNode").value
		let b=document.getElementById("endNode").value

		let start=nodes.find(n=>n.username===a)
		let end=nodes.find(n=>n.username===b)

		if(!start||!end) return

		let path=GraphAlgorithms.bfsShortestPath(start.id,end.id,adj)

		window.currentPath=path

	}

}

}
