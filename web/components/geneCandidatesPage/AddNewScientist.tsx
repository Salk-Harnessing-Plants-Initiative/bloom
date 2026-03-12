import { useState } from "react";
import * as TypeDefs from "../../types/genecandidates";
import {
    Box,
    Button,
    Popover,
    TextField,
    Autocomplete,
    Typography,
} from "@mui/material";

export default function AddNewScientist({ peopleList, setPeopleList }: { setPeopleList: React.Dispatch<React.SetStateAction<TypeDefs.People[]>>, peopleList: TypeDefs.People[] }) {
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
    const [newScientistName, setNewScientistName] = useState("");
    const [newScientistEmail, setNewScientistEmail] = useState("");
    const [message, setMessage] = useState<string | null>(null);


    const handleClick = (event: React.MouseEvent<HTMLElement>) => {
        setAnchorEl(event.currentTarget);
    };

    const handleClose = () => {
        setAnchorEl(null);
        setNewScientistName("");
        setNewScientistEmail("");
        setMessage(null);
    };

    const handleAddPeople = () => {
        setPeopleList(prev => [
          ...prev,
          { id: 0, name: newScientistName, email: newScientistEmail }
        ]);
        setMessage("Successfully added new scientist!");
        setNewScientistName("");
        setNewScientistEmail("");
      };

    const open = Boolean(anchorEl);
    const id = open ? "add-category-popover" : undefined;

    return (
        <Box sx={{ height: '90%', p: 0 }}>
            <Button
                variant="outlined"
                onClick={handleClick}>
                Add New Scientist
            </Button>

            <Popover
                id={id}
                open={open}
                anchorEl={anchorEl}
                onClose={handleClose}
                anchorOrigin={{
                    vertical: "bottom",
                    horizontal: "left",
                }}
            >
                <Box sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1 }}>
                    <TextField
                        label="New Scientist Name"
                        value={newScientistName}
                        onChange={(e) => setNewScientistName(e.target.value)}
                        size="small"
                    />

                    <TextField
                        label="New Scientist Email"
                        value={newScientistEmail}
                        onChange={(e) => setNewScientistEmail(e.target.value)}
                        size="small"
                    />

                    <Button
                    variant="contained"
                    onClick={handleAddPeople}
                    disabled={!newScientistName || !newScientistEmail}
                    sx={{
                        color: "white",
                        backgroundColor: "#1976d2 !important",
                        "&:hover": {
                        backgroundColor: "#1565c0",
                        },
                        "&.Mui-disabled": {
                        backgroundColor: "#b0bec5", 
                        color: "#eeeeee",
                        },
                    }}
                    >
                    Add
                    </Button>

                    {message && (
                        <Typography variant="subtitle2">{message}</Typography>
                    )}
                </Box>
            </Popover>

        </Box>
    );
}