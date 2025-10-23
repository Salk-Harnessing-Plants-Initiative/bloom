import {
    //   createServerSupabaseClient,
    getUser,
} from "@salk-hpi/bloom-nextjs-auth";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import type { SupabaseClient } from "@supabase/supabase-js";
import { useEffect, useState, Fragment, useRef } from "react";
import type { Database } from "@/lib/database.types";
import Modal from '@mui/material/Modal';
import Box from '@mui/material/Box';
import IconButton from "@mui/material/IconButton";
import CloseIcon from '@mui/icons-material/Close';
import Typography from '@mui/material/Typography';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import SendIcon from '@mui/icons-material/Send';
import InsertLinkIcon from '@mui/icons-material/InsertLink';
import AddPhotoAlternateIcon from '@mui/icons-material/AddPhotoAlternate';
import AddLabels from "./AddLabels";
import LabelIcon from '@mui/icons-material/Label';
import Tooltip from "@mui/material/Tooltip";
import * as GeneTypes from '../../types/genecandidates';

import {
    MenuItem,
    Checkbox,
    FormControlLabel,
    Select,
    InputLabel,
    FormControl,
    SelectChangeEvent,
} from "@mui/material";

type PersonRow = Database["public"]["Tables"]["people"]["Row"];

type GeneRow = {
    category: string | null;
    disclosed_to_otd: boolean | null;
    evidence_description: string | null;
    experiment_plans_and_progress: string | null;
    status: string;
    gene: string;
    publication_status: boolean | null;
    translation_approval_date: string | null;
    people: PersonRow[];
    genes: {
        short_id: string | null;
        symbol: string | null;
        assemblies: {
            hpi_reference_id: string | null;
        } | null
        standard_name: string | null;
    } | null;
};

export default function Progress({ candidate, candidates_list, currentGeneCandidate, isOpen, handleOpen, handleExperimentLogsClose, handleCandidateChange }: { candidate: GeneRow, candidates_list: GeneRow[], currentGeneCandidate: GeneRow | null, isOpen: boolean, handleOpen: () => void, handleExperimentLogsClose: () => void, handleCandidateChange: (geneId: string) => void }) {
    const [progressLogs, setProgressLogs] = useState<GeneTypes.Logs[]>([]);
    const [filteredLogs, setFilteredLogs] = useState<GeneTypes.Logs[]>([]);
    const [newUpdate, setNewUpdate] = useState("");
    const supabase = createClientComponentClient<Database>() as unknown as SupabaseClient<Database>;
    const bottomRef = useRef<HTMLDivElement>(null);
    const [uploadedImages, setUploadedImages] = useState<File[]>([]);
    const [showLinkInput, setShowLinkInput] = useState(false);
    const [newLink, setNewLink] = useState("");
    const [newLinkText, setNewLinkText] = useState<string | null>(null);
    const [attachedLinks, setAttachedLinks] = useState<{ url: string, text: string | null }[]>([]);
    const [labelanchorEl, setLabelAnchorEl] = useState<null | HTMLElement>(null);
    const [selectedTags, setSelectedTags] = useState<GeneTypes.Tag[]>([]);
    const [filteredTags, setFilteredTags] = useState<string | null>(null);

    const handleClose = () => {
        setUploadedImages([]);
        setAttachedLinks([]);
        setNewUpdate("");
        handleExperimentLogsClose();
    }

    /* Open Close handles on AddLable Comp. */
    const handleOpenTagPopover = (event: React.MouseEvent<HTMLElement>) => {
        setLabelAnchorEl(event.currentTarget);
    };
    const handleCloseTagPopover = () => {
        setLabelAnchorEl(null);
    };

    const handleTagsFilter = (tag: string) => {
        if (filteredTags === tag) {
          setFilteredTags(null);
          setFilteredLogs(progressLogs);
        } else {          
          setFilteredTags(tag);
          const filtered = progressLogs.filter((log) =>
            log.tags?.some((t) => t.label === tag)
          );
          setFilteredLogs(filtered);
        }
      };

    useEffect(() => {
        if(!isOpen) return;
        
        if (bottomRef.current) {
            bottomRef.current.scrollIntoView({ behavior: "smooth" });
        }
        setFilteredLogs(progressLogs);
    }, [progressLogs]);

    useEffect(() => {
        if(!currentGeneCandidate) return;
        if(!isOpen) return;

        fetchMessages();
    }, [currentGeneCandidate]);

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

    const handleSend = async () => {
        if (!newUpdate.trim() || !currentGeneCandidate) return;

        let user_email = await fetchUser();

        const newGeneLog: GeneTypes.Logs = {
            gene: currentGeneCandidate.gene,
            timestamp: new Date().toISOString(),
            user_email: user_email,
            message: newUpdate.trim(),
            images: [],
            links: [],
            tags: selectedTags.map(tag => ({ label: tag.label, color: tag.color }))
        };

        //Image Upload
        let uploadedImageUrls: string[] = [];
        for (const file of uploadedImages) {
            let clean_fname = file.name.replace(/\s+/g, "_").replace(/[^\w.-]/g, "");
            const fileExtension = clean_fname.substring(clean_fname.lastIndexOf('.'));
            const baseFileName = clean_fname.substring(0, clean_fname.lastIndexOf('.'));
            const uniqueSuffix = `${Date.now()}_${Math.floor(Math.random() * 10000)}`;
            const finalFileName = `${baseFileName}_${uniqueSuffix}${fileExtension}`;

            const { data, error } = await supabase.storage
                //.from('experiment-log-images')
                .from('images-expriment-logs')
                .upload(`exp-progress-logs/${finalFileName}`, file);

            if (error) {
                console.error("Failed to upload image:", error);
                continue;
            }

            const { data: publicUrlData } = supabase.storage
                //.from('experiment-log-images')
                .from('images-expriment-logs')
                .getPublicUrl(`exp-progress-logs/${finalFileName}`);
            uploadedImageUrls.push(publicUrlData.publicUrl);
        }
        newGeneLog.images = uploadedImageUrls;
        newGeneLog.links = attachedLinks;
        
        const { data, error } = await supabase.from("experiment_progress_logs").insert([
            {
                ...newGeneLog,
                timestamp: typeof newGeneLog.timestamp === "string"
                    ? newGeneLog.timestamp
                    : newGeneLog.timestamp.toISOString()
            }
        ]);

        if (error) {
        console.error("Error inserting log:", error);
        return;
        }
        
        setProgressLogs((prevLogs) => [
            ...prevLogs,
            newGeneLog
        ]);

        setNewLink("");
        setNewLinkText(null);
        setUploadedImages([]);
        setAttachedLinks([]);
        setShowLinkInput(false);
        setNewUpdate("");
        setSelectedTags([]);
    };

    const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files;
        if (!files) return;
        const selectedFiles = Array.from(files);
        setUploadedImages((prev) => [...prev, ...selectedFiles]);
    };

    const handleAttachLink = async () => {
        setShowLinkInput(true);
    }

    const fetchMessages = async () => {
        if (!currentGeneCandidate) return;

        const { data, error } = await supabase
            .from("experiment_progress_logs")
            .select("*")
            .eq("gene", currentGeneCandidate.gene)
            .order("timestamp", { ascending: false });

        if (error) {
            console.error("Error fetching progress logs:", error);
            setProgressLogs([]);
        }
        else {
            const normalizedLogs = data.map((log: any) => ({
                ...log,
                user_email: log.user_email ?? "Unknown"
            }));
            setProgressLogs(normalizedLogs);
            setFilteredLogs(normalizedLogs);
        } 
    }

    return (
        <div className="container">
            <div
                className="hover-target cursor-default bg-white rounded-md border border-gray-300 text-sm p-1 hover:border-blue-500"
                onClick={handleOpen}
            >
                Progress
            </div>

            <Modal open={isOpen} onClose={handleClose}>
                <Box
                    sx={{
                        width: "65%",
                        height: "80%",
                        bgcolor: "white",
                        borderRadius: 2,
                        p: 3,
                        boxShadow: 24,
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        display: "flex",
                        flexDirection: "column",
                    }}
                >
                    <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                        <IconButton onClick={handleClose}>
                            <CloseIcon />
                        </IconButton>
                    </Box>
                    <Box sx={{ display: "flex", flexDirection: "column", justifyContent: "center", mb: 2, alignItems: "center" }}>
                        <Typography variant="h6" gutterBottom>
                            Experiment Progress Logs
                        </Typography>
                        <Typography
                            variant="subtitle1"
                            gutterBottom
                        >
                            Gene Candidate : {currentGeneCandidate?.gene || "No candidate selected"}
                        </Typography>

                        <Select
                            fullWidth
                            value={currentGeneCandidate?.gene || ""}
                            onChange={(e) => handleCandidateChange(e.target.value)}
                            size="small"
                        >
                            {candidates_list?.map((candidate, index) => (
                                <MenuItem key={index} value={candidate.gene}>
                                    {candidate.gene}
                                </MenuItem>
                            ))}
                        </Select>
                    </Box>
                    {/* <Box sx={{ borderBottom: "2px solid rgb(2, 20, 54)", my: 2 }} /> */}
                    <Box
                        sx={{
                            flex: 1,
                            overflowY: "auto",
                            pr: 1,
                        }}
                    >
                        {filteredLogs && filteredLogs.length > 0 ? (
                            filteredLogs.map((log, index) => (
                                <Box
                                    key={index}
                                    sx={{
                                        position: "relative",
                                        mb: 4,
                                        maxWidth: "100%",
                                    }}
                                >
                                    <Box sx={{ display: "flex", alignItems: "center", mt: 1, ml: 1, gap: 1 }}>
                                        <Typography variant="subtitle1" color="text.secondary">
                                            • {log.user_email}
                                        </Typography>
                                        <Typography variant="subtitle1" color="text.secondary">
                                            • {log.timestamp && new Date(log.timestamp).toLocaleString('en-US', {
                                                hour: 'numeric',
                                                minute: 'numeric',
                                                hour12: true,
                                                day: 'numeric',
                                                month: 'short',
                                                year: 'numeric'
                                                })}
                                        </Typography>
                                        <Box sx={{ display: "flex", gap: 1, ml: 'auto' }}>
                                            {log.tags?.map((tag: { label: string; color: string }, i: number) => (
                                            <Box
                                                key={i}
                                                onClick={() =>
                                                    handleTagsFilter(tag.label)
                                                }
                                                sx={{
                                                px: 1.2,
                                                py: 0.3,
                                                borderRadius: 2,
                                                backgroundColor: `${tag.color}`,
                                                color: '#fff',
                                                fontSize: 12,
                                                fontWeight: 500,
                                                // textTransform: "uppercase",
                                                cursor: "pointer",
                                                border: filteredTags === tag.label ? `3px solid black` : "1px solid rgba(0, 0, 0, 0.3)",
                                                boxShadow: '0 1px 3px rgba(0, 0, 0, 0.8)',
                                                textShadow: '1px 1px 2px rgba(0, 0, 0, 1.9)',
                                                transition: "all 0.2s ease-in-out",
                                                "&:hover": {
                                                    opacity: 0.8,
                                                  },
                                                }}
                                            >
                                                {tag.label}
                                            </Box>
                                            ))}
                                        </Box>
                                    </Box>

                                    <Box
                                        sx={{
                                            backgroundColor: "#f1f5f9",
                                            borderRadius: 2,
                                            p: 2,
                                            fontStyle: "italic",
                                            boxShadow: "10px 7px 4px rgba(0,0,0,0.08)",
                                        }}
                                    >
                                        <Typography variant="body1" sx={{ whiteSpace: "pre-line" }}>
                                            {log.message}
                                        </Typography>

                                        {log.images?.length > 0 && (
                                            <Box sx={{ mt: 2, display: "flex", flexWrap: "wrap", gap: 1 }}>
                                                {log.images.map((url: string, i: number) => (
                                                    <a
                                                        key={i}
                                                        href={url}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        style={{ display: 'inline-block', borderRadius: '4px' }}
                                                    >
                                                        <Box
                                                            component="img"
                                                            src={url}
                                                            alt={`Attached image ${i + 1}`}
                                                            sx={{
                                                                maxHeight: 150,
                                                                maxWidth: 150,
                                                                borderRadius: 1,
                                                                objectFit: "cover",
                                                                border: "1px solid #ccc",
                                                                mb: 2,
                                                                boxShadow: "10px 4px 12px rgba(8, 72, 149, 0.4)",
                                                                cursor: "pointer",
                                                                transition: "0.2s",
                                                                "&:hover": {
                                                                    border: "2px solid #1e88e5",
                                                                    boxShadow: "0 0 0 2px rgba(30, 136, 229, 0.3)",
                                                                },
                                                            }}
                                                        />
                                                    </a>
                                                ))}
                                            </Box>
                                        )}

                                        {log.links?.length > 0 && (
                                            <Box
                                                sx={{
                                                    mt: 2,
                                                    display: "flex",
                                                    flexWrap: "wrap",
                                                    gap: 1
                                                }}
                                            >
                                                {log.links.map((link, i) => (
                                                    <a
                                                        key={i}
                                                        href={link.url}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        style={{
                                                            color: "#2563eb",
                                                            textDecoration: "underline",
                                                            whiteSpace: "nowrap",
                                                            padding: "4px 8px",
                                                            backgroundColor: "#e0e7ff",
                                                            borderRadius: "9999px",
                                                            fontSize: "0.875rem",
                                                            display: "inline-block"
                                                        }}
                                                    >
                                                        🔗 {link.text || link.url}
                                                    </a>
                                                ))}
                                            </Box>
                                        )}
                                    </Box>
                                </Box>
                            ))
                        ) : (
                            <Typography
                                variant="body2"
                                color="text.secondary"
                                sx={{ textAlign: "center", mt: 2, fontStyle: "italic" }}
                            >
                                No logs yet.
                            </Typography>
                        )}


                        <div ref={bottomRef} />
                    </Box>

                    {/* Upload Preview Section */}

                    <Box
                        sx={{
                            gap: 1,
                            pt: 2,
                            borderTop: "2px solid rgb(86, 88, 92)",
                            mt: 2,
                        }}
                    >
                        {selectedTags.length > 0 && (
                            <Box
                                sx={{
                                    display: 'flex',
                                    flexWrap: 'wrap',
                                    gap: 1,
                                }}>
                                {selectedTags.map((tag, index) => (
                                    <Box
                                        key={index}
                                        sx={{
                                            backgroundColor: tag.color,
                                            color: 'white',
                                            padding: '2px 8px',
                                            borderRadius: '3px',
                                            fontSize: '0.875rem',
                                            display: 'inline-block',
                                            border: '1px solid rgba(0, 0, 0, 0.3)', 
                                            boxShadow: '0 1px 3px rgba(0, 0, 0, 0.8)', 
                                            textShadow: '1px 1px 2px rgba(0, 0, 0, 1.9)',
                                        }}
                                    >
                                        {tag.label}
                                    </Box>
                                ))}
                            </Box>
                        )}

                        {uploadedImages.length > 0 && (
                            <Box
                                sx={{
                                    display: 'flex',
                                    flexWrap: 'wrap',
                                    gap: 1,
                                }}>

                                {
                                    uploadedImages.map((file, index) => (
                                        <Box
                                            key={index}
                                            component="img"
                                            src={URL.createObjectURL(file)}
                                            alt={`Preview ${index + 1}`}
                                            sx={{
                                                width: 100,
                                                height: 100,
                                                objectFit: 'cover',
                                                borderRadius: 2,
                                                border: '1px solid #ccc',
                                                mb: 2,
                                                boxShadow: "10px 4px 12px rgba(8, 72, 149, 0.4)",
                                            }}
                                        />
                                    ))}
                            </Box>
                        )}
                    </Box>

                    {attachedLinks.length > 0 && (
                        <Box
                            sx={{
                                mt: 2,
                                p: 2,
                                display: "flex",
                                overflowX: "auto",
                                gap: 1,
                                bgcolor: "#f9f9f9",
                                borderRadius: 2,
                                maxWidth: "100%",
                            }}
                        >
                            {attachedLinks.map((link, index) => (
                                <Box
                                    component="a"
                                    key={index}
                                    href={link.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    sx={{
                                        display: "inline-block",
                                        backgroundColor: "#e0e7ff",
                                        color: "#2563eb",
                                        padding: "8px 16px",
                                        borderRadius: "9999px",
                                        fontSize: "0.875rem",
                                        textDecoration: "none",
                                        border: "2px solid transparent",
                                        transition: "all 0.2s ease",
                                        "&:hover": {
                                            border: "2px solidrgb(12, 57, 154)",
                                            backgroundColor: "#c7d2fe",
                                        },
                                    }}
                                >
                                    🔗 {link.text || link.url}
                                </Box>

                            ))}
                        </Box>
                    )}

                    {showLinkInput && (
                        <Box
                            sx={{
                                mt: 1,
                                display: 'flex',
                                gap: 1,
                                borderRadius: "3px",
                                mb: 2,
                            }}>
                            <TextField
                                size="small"
                                placeholder="Display text (optional)..."
                                value={newLinkText || ""}
                                onChange={(e) => setNewLinkText(e.target.value)}
                                fullWidth
                            />
                            <TextField
                                size="small"
                                placeholder="Paste a link..."
                                value={newLink}
                                onChange={(e) => setNewLink(e.target.value)}
                                fullWidth
                            />
                            <Button
                                variant="contained"
                                sx={{
                                    backgroundColor: '#1f69f2 !important',
                                    color: 'white',
                                    '&:hover': {
                                        backgroundColor: '#1249b0 !important',
                                    },
                                }}
                                onClick={() => {
                                    if (newLink.trim() !== "") {
                                        setAttachedLinks(prev => [
                                            ...prev,
                                            {
                                                text: newLinkText?.trim() || newLink.trim(),
                                                url: newLink.trim()
                                            }
                                        ]);
                                        setNewLink("");
                                        setNewLinkText(null);
                                        setShowLinkInput(false);
                                    }
                                }}
                            >
                                Add
                            </Button>
                            <Button
                                variant="text"
                                onClick={() => {
                                    setNewLink("");
                                    setNewLinkText(null);
                                    setShowLinkInput(false);
                                }}
                            >
                                Cancel
                            </Button>
                        </Box>
                    )}

                    <Box
                        sx={{
                            display: "flex",
                            alignItems: "center",
                            gap: 1,
                            pt: 2,
                            // borderTop: "1px solid #ddd",
                            // mt: 2,
                        }}
                    >
                        <TextField
                            fullWidth
                            inputProps={{ maxLength: 2000 }}
                            size="small"
                            placeholder="Type a message to log your experiment progress..."
                            value={newUpdate}
                            onChange={(e) => setNewUpdate(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") handleSend();
                            }}
                        />

                        <Tooltip title="Add images" placement="top" arrow>
                        <IconButton component="label" sx={{ bgcolor: "#f1f5f9" }}>
                            <AddPhotoAlternateIcon />
                            <input type="file" hidden accept="image/*" onChange={handleImageUpload} />
                        </IconButton>
                        </Tooltip>
                        
                        <Tooltip title="Attach Links" placement="top" arrow>
                        <IconButton onClick={handleAttachLink} sx={{ bgcolor: "#f1f5f9" }}>
                            <InsertLinkIcon />
                        </IconButton>
                        </Tooltip>

                        <Tooltip title="Add Labels" placement="top" arrow>
                        <IconButton onClick={handleOpenTagPopover} sx={{ bgcolor: "#f1f5f9" }}>
                            <LabelIcon />
                        </IconButton>
                        </Tooltip>

                        <AddLabels
                            anchorEl={labelanchorEl}
                            handleCloseTagPopover={handleCloseTagPopover}
                            setSelectedTags={setSelectedTags}
                            selectedTags={selectedTags}
                        />
                        <Button
                            variant="contained"
                            sx={{
                                backgroundColor: '#1f69f2 !important',
                                color: 'white',
                                '&:hover': {
                                    backgroundColor: '#1249b0 !important',
                                },
                            }}
                            endIcon={<SendIcon />}
                            onClick={handleSend}
                            disabled={newUpdate.trim() === ""}
                        >
                            Send
                        </Button>
                    </Box>
                </Box>
            </Modal>

            <div
                className="reveal-on-hover text-sm bg-white border border-gray-600 p-2 rounded-md w-96 ml-1 shadow z-10 max-h-40 overflow-auto cursor-pointer"
                onClick={handleOpen}
            >
                <span className="italic">Click to view</span>

                {/* {progressLogs && progressLogs.length > 0 ? (
            progressLogs.map((log, index) => (
              <div key={index} className="mb-2">
                <div className="font-medium text-gray-800">{log.user}</div>
                <div className="text-gray-600 truncate">{log.message}</div>
              </div>
            ))
          ) : (
            <span className="italic"> No Logs.</span>
          )} */}
            </div>
        </div>
    );
}