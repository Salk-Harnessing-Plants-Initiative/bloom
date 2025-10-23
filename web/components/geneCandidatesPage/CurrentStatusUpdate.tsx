import { useEffect, useState } from "react";
import { Modal, Box } from "@mui/material";
import CloseIcon from '@mui/icons-material/Close';
import Typography from '@mui/material/Typography';
import type { Database } from "@/lib/database.types";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import type { SupabaseClient } from "@supabase/supabase-js";
import { use } from "react";

export default function CurrentStatus({
  open,
  setOpen,
  selectedGene,
}: {
  open: boolean;
  setOpen: (value: boolean) => void;
  selectedGene : string | null;
}) {
  const style = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: '60%',
    height: 500,
    bgcolor: 'background.paper',
    border: '2px solid #000',
    borderRadius: '16px',
    boxShadow: 24,
    p: 4,
  };

const statusColors = {
    stopped: { textColor: "text-red-700", bgColor: "bg-red-100" },
    "suspected": {
      textColor: "text-orange-700",
      bgColor: "bg-orange-100",
    },
    "under-investigation": {
      textColor: "text-yellow-700",
      bgColor: "bg-yellow-100",
    },
    "in-translation": { textColor: "text-lime-700", bgColor: "bg-lime-100" },
    "translation-confirmed": {
      textColor: "text-blue-700",
      bgColor: "bg-blue-100",
    },
};

  const supabase = createClientComponentClient<Database>() as unknown as SupabaseClient<Database>;;
  const [statusLogs, setStatusLogs] = useState<any[]>([]);
  const [newStatus, setNewStatus] = useState("");


  const fetchStatusLogs = async () => {
    if (!selectedGene) {
      setStatusLogs([]);
      return;
    }
    const { data, error } = await supabase
      .from("gene_candidates")
      .select("status_logs")
      .eq("gene", selectedGene);

    if (error) {
      console.error("Error fetching status logs:", error);
      return;
    }

    const flattenedLogs = data.flatMap((entry) => entry.status_logs || []);
    setStatusLogs(flattenedLogs);
    // console.log("Flattened status logs:", data);
  };

  const fetchUser = async (): Promise<string> => {
    try {
        const {
            data: { user },
            error,
        } = await supabase.auth.getUser();
        if (error || !user) {
            console.error("Failed to get user:", error);
            return "Unknown";
        }
        return user.email || user.id || "Unknown";
    } catch (err) {
        console.error("Unexpected error while fetching user:", err);
        return "Unknown";
    }
  };

  const handleStatusChange = async () => {
    if (!newStatus || !selectedGene) return;

    let user_email = await fetchUser();

    const newLog = {
      status: newStatus,
      userid: user_email,
      date: new Date().toISOString().split("T")[0],
    };

    const updatedLogs = [...statusLogs, newLog];

    const { error } = await supabase
      .from("gene_candidates")
      .update({ status_logs: updatedLogs })
      .eq("gene", selectedGene);

    if (!error) {
      setStatusLogs(updatedLogs);
      setNewStatus("");
    } else {
      console.error("Failed to update status:", error);
    }
  };
  
  useEffect(() => {
    console.log(selectedGene," selectedGene");
    fetchStatusLogs();
  }, [selectedGene]);

  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      aria-labelledby="modal-modal-title"
      aria-describedby="modal-modal-description"
    >   
      <Box sx={style}>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
              <CloseIcon onClick={() => setOpen(false)} sx={{ cursor: 'pointer' }} />
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
              <Typography id="modal-modal-title" variant="h6" component="h2">
                STATUS UPDATE LOGS
              </Typography>
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
              <Typography id="subtitle-2" variant="h6" component="h2">
                {selectedGene}
              </Typography>
        </Box>

        {/* <Box className="space-y-2">
        {[...statusLogs].reverse().map((log, index) => (
            <div key={index} className="p-2 rounded-md bg-gray-100 shadow-sm">
            <p className="text-sm font-medium">Status: {log.status}</p>
            <p className="text-xs text-gray-600">User: {log.userid}</p>
            <p className="text-xs text-gray-600">Date: {log.date}</p>
            </div>
        ))}
        </Box> */}

        {/* Status Change Section */}
        <div className="mb-4 p-4 bg-gray-50 rounded-md shadow-sm border border-gray-200">
        
        
            <div className="flex flex-row items-center gap-4">
                <h3 className="text-sm font-semibold text-gray-700 mb-2">Change Current Status</h3>
                <select
                value={newStatus}
                onChange={(e) => setNewStatus(e.target.value)}
                className="text-sm p-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400"
                >
                <option value="">Select new status</option>
                {Object.keys(statusColors).map((statusKey) => (
                    <option key={statusKey} value={statusKey}>
                    {statusKey}
                    </option>
                ))}
                </select>

                <button
                onClick={handleStatusChange}
                className="bg-blue-600 text-white text-sm px-4 py-2 rounded-md hover:bg-blue-700"
                >
                Update
                </button>
            </div>
        </div>


        <Box className="space-y-2">
        {[...statusLogs]
            .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime()) // Ensure sorted by date descending
            .map((log, index) => {
            const statusColor = statusColors[log.status as keyof typeof statusColors] || {
                textColor: "text-gray-700",
                bgColor: "bg-gray-100",
            };

            const isLatest = index === 0;

            return (
                <div
                key={index}
                className="p-3 bg-gray-100 shadow-sm flex flex-row items-start gap-4 rounded-md"
                >
                {/* Left Column: Status Button */}
                <div className="flex flex-col items-start">
                    <button
                    className={`text-xs font-semibold w-48 px-3 py-1 rounded-full ${statusColor.textColor} ${statusColor.bgColor}`}
                    disabled
                    >
                    {log.status}
                    </button>
                </div>

                {/* Right Column: User and Date */}
                <div className="flex flex-col">
                    <p className="text-xs text-gray-800 font-medium">By: {log.userid}</p>
                    <p className="text-xs text-gray-600">On: {log.date}</p>
                    {isLatest && (
                    <span className="text-[10px] text-green-600 mt-1 font-medium">
                        Current status
                    </span>
                    )}
                </div>
                </div>
            );
            })}
        </Box>


      </Box>
    </Modal>
  );
}


// type Status =
//   | "stopped"
//   | "suspected"
//   | "under-investigation"
//   | "in-translation"
//   | "translation-confirmed";




// function Status({
//     status,
//     handleCurrentStatus,
//     geneCandidate,
//     setSelectedGene,
//   }: {
//     status: Status;
//     handleCurrentStatus: (value: boolean) => void;
//     geneCandidate : string;
//     setSelectedGene : (value: string) => void;
//   }) {
//     const colors = statusColors[status];
  
//     return (
//       <div className="">
//         <div
//           onClick={() => {
//             handleCurrentStatus(true);
//             setSelectedGene(geneCandidate);
//           }}
//           className={
//             `rounded-md inline border border-gray-300 text-sm px-3 py-1 
//             ${colors.textColor} ${colors.bgColor}
//             cursor-pointer transition duration-200 
//             hover:brightness-95 hover:shadow-sm`
//           }
//         >
//           {status}
//         </div>
//       </div>
//     );
// }


